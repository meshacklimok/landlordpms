"""Who used how much water and electricity in a month (D-145).

Built from the approved readings taken in the month. Each lease's units are its part of the meter's use as the
reading was divided on approval (the whole use for a room's own meter), and its amount is what it was actually
charged. What no tenant pays (empty rooms, a meter the landlord pays, the rest after rounding) is the landlord's.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Sum
from django.utils.translation import gettext as _

from core.money import ZERO

from . import services
from . import split as splitting
from .models import Meter, MeterCharge, MeterReading

# The figures are kept under these keys, so a template can say row.use.water.units.
KEYS = {Meter.Utility.WATER: "water", Meter.Utility.ELECTRICITY: "power"}
UTILITIES = tuple(KEYS.values())


def _zero() -> dict:
    return {u: {"units": ZERO, "amount": ZERO} for u in UTILITIES}


@dataclass
class Row:
    """One room and the tenant there; a room let to two leases in the month has a row for each."""

    unit: object
    tenant: str
    use: dict = field(default_factory=_zero)

    @property
    def amount(self) -> Decimal:
        return sum((v["amount"] for v in self.use.values()), ZERO)


@dataclass
class Section:
    property: object
    rows: list = field(default_factory=list)
    landlord: dict = field(default_factory=_zero)
    bill: dict = field(default_factory=_zero)

    @property
    def tenants(self) -> dict:
        out = _zero()
        for row in self.rows:
            for u in UTILITIES:
                out[u]["units"] += row.use[u]["units"]
                out[u]["amount"] += row.use[u]["amount"]
        return out

    @property
    def footer(self) -> list:
        return [(_("Tenants"), self.tenants), (_("Landlord"), self.landlord), (_("Bill"), self.bill)]


def month_bounds(month: datetime.date) -> tuple[datetime.date, datetime.date]:
    start = month.replace(day=1)
    end = (start + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
    return start, end


def readings_in(membership, month: datetime.date, prop=None):
    start, end = month_bounds(month)
    qs = (services.visible_readings(membership)
          .filter(status=MeterReading.Status.APPROVED, is_baseline=False, read_on__range=(start, end))
          .select_related("meter__property", "previous").order_by("meter__property__name", "read_on", "pk"))
    return qs.filter(meter__property=prop) if prop is not None else qs


def report(membership, month: datetime.date, prop=None) -> list[Section]:
    """One section per property, rooms in code order."""
    readings = [r for r in readings_in(membership, month, prop) if services.billable(r)]
    charged = defaultdict(lambda: ZERO)
    for reading_id, lease_id, unit_id, amount in (
            MeterCharge.objects.filter(reading__in=readings, cancelled_at__isnull=True)
            .values_list("reading_id", "lease_id", "unit_id").annotate(t=Sum("amount"))):
        charged[(reading_id, lease_id, unit_id)] = amount
    sections: dict[int, Section] = {}
    rows: dict[tuple, Row] = {}
    for reading in readings:
        meter = reading.meter
        section = sections.setdefault(meter.property_id, Section(meter.property))
        u = KEYS[meter.utility]
        division = splitting.divide(reading, split=reading.split or None, vacant_share=reading.vacant_share or None,
                                    rate=reading.rate, minimum=reading.minimum_charge, bill_total=reading.bill_total)
        section.bill[u]["units"] += reading.usage
        section.bill[u]["amount"] += division.total
        paid, units = ZERO, ZERO
        if division.available:
            for room in division.rooms:
                for part in room.parts:
                    row = rows.get((room.unit.pk, part.lease.pk))
                    if row is None:
                        row = rows[(room.unit.pk, part.lease.pk)] = Row(room.unit, part.tenant)
                        section.rows.append(row)
                    amount = charged.get((reading.pk, part.lease.pk, room.unit.pk), ZERO)
                    row.use[u]["units"] += part.units
                    row.use[u]["amount"] += amount
                    paid += amount
                    units += part.units
        section.landlord[u]["units"] += reading.usage - units
        section.landlord[u]["amount"] += max(ZERO, division.total - paid)
    out = sorted(sections.values(), key=lambda s: s.property.name.lower())
    for section in out:
        section.rows.sort(key=lambda r: (r.unit.code.lower(), r.tenant.lower()))
    return out


def totals(sections: list[Section]) -> dict:
    """The month across every property: the bill, what tenants were charged and what fell on the landlord."""
    out = {"bill": _zero(), "tenants": _zero(), "landlord": _zero()}
    for section in sections:
        for key, figures in (("bill", section.bill), ("tenants", section.tenants), ("landlord", section.landlord)):
            for u in UTILITIES:
                out[key][u]["units"] += figures[u]["units"]
                out[key][u]["amount"] += figures[u]["amount"]
    return out
