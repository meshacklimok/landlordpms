"""Are the meters read for the invoices of a month? (D-146)

A reading taken in a month is billed on the next month's invoice (D-057). A meter that was not read, or whose
reading still waits for approval, leaves its water or power off that invoice; it follows on a separate invoice
once approved. This tells the person billing which meters those are, before they bill. A meter the landlord pays
(water free to every tenant, or a room's own meter that only decides a split) bills nothing, so it is never waited for.
"""

import datetime
from dataclasses import dataclass, field

from accounts.models import Membership
from billing import invoicing

from . import services
from .models import Meter, MeterReading

Status = MeterReading.Status


@dataclass
class Check:
    month: datetime.date  # the month being billed
    since: datetime.date  # readings from this day count: the first of the month before
    ready: list[Meter] = field(default_factory=list)
    waiting: list[Meter] = field(default_factory=list)  # a reading is in, not approved yet
    missing: list[Meter] = field(default_factory=list)  # no reading since `since`
    free: list[Meter] = field(default_factory=list)  # the landlord pays: nothing is billed, so nothing is waited for

    @property
    def total(self) -> int:
        return len(self.ready) + len(self.waiting) + len(self.missing)

    @property
    def complete(self) -> bool:
        return not self.waiting and not self.missing

    @property
    def problems(self) -> list[tuple]:
        """(property, waiting meters, missing meters) for each property with something left to do."""
        out: dict[int, list] = {}
        for status, meters in ((1, self.waiting), (2, self.missing)):
            for meter in meters:
                out.setdefault(meter.property_id, [meter.property, [], []])[status].append(meter)
        return sorted(out.values(), key=lambda row: row[0].name)


def check(membership: Membership, month: datetime.date) -> Check:
    """Each metered, billed meter that serves a unit let during `month`, by whether it has been read for it."""
    month = invoicing.month_start(month)
    since = invoicing.add_months(month, -1)
    end = invoicing.add_months(month, 1) - datetime.timedelta(days=1)
    result = Check(month=month, since=since)
    meters = (services.visible_meters(membership).filter(kind=Meter.Kind.POSTPAID)
              .select_related("property").prefetch_related("served__unit").order_by("property__name", "label"))
    meters = list(meters)
    latest: dict[int, set[str]] = {}
    for meter_id, status in (MeterReading.objects.filter(meter__in=meters, status__in=MeterReading.LIVE,
                                                         read_on__gte=since)
                             .values_list("meter_id", "status")):
        latest.setdefault(meter_id, set()).add(status)
    for meter in meters:
        units = [mu.unit for mu in meter.served.all()]
        if not services.covering_leases(units, month, end).exists():
            continue
        if meter.landlord_pays:  # e.g. water included in the rent: readings only follow the use (D-146)
            result.free.append(meter)
            continue
        seen = latest.get(meter.pk, set())
        if Status.SUBMITTED in seen:
            result.waiting.append(meter)
        elif Status.APPROVED in seen:
            result.ready.append(meter)
        else:
            result.missing.append(meter)
    return result
