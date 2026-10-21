"""The owner's properties as they are today (D-217): money owed, empty units, repairs and leases ending.

Everything starts from `selectors.properties(account)`. Tenants are never named: rows carry the unit and numbers.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count
from django.utils import timezone

from billing import selectors as billing
from billing.models import LedgerEntry
from leases.models import EXPIRING_WITHIN_DAYS, Lease
from leases.services import with_occupancy
from maintenance.models import MaintenanceRequest
from properties.models import Unit

from . import selectors
from .models import OwnerAccount

ZERO = Decimal("0")
REPAIRS_SHOWN = 20


@dataclass
class PropertyLive:
    property: object
    units: int = 0
    let: int = 0
    owing_leases: int = 0
    owed: Decimal = ZERO
    over_60: Decimal = ZERO
    open_repairs: int = 0
    ending: int = 0
    empty_units: list = field(default_factory=list)

    @property
    def vacant(self) -> int:
        return self.units - self.let

    @property
    def occupancy(self) -> int:
        return round(100 * self.let / self.units) if self.units else 0


def live(account: OwnerAccount, today: datetime.date | None = None) -> dict:
    """Per-property rows, the totals, repairs still open and leases ending soon."""
    today = today or timezone.localdate()
    props = list(selectors.properties(account))
    rows = {p.pk: PropertyLive(p) for p in props}
    for unit in with_occupancy(Unit.objects.filter(property__in=props), today):
        row = rows[unit.property_id]
        row.units += 1
        if unit.is_occupied:
            row.let += 1
        else:
            row.empty_units.append(unit.code)
    entries = LedgerEntry.objects.for_org(account.organization).filter(lease__unit__property__in=props)
    for arrears in billing._arrears_from(entries, today, overdue_only=True, with_tenants=False):
        row = rows[arrears.lease.unit.property_id]
        row.owing_leases += 1
        row.owed += arrears.balance
        row.over_60 += arrears.buckets.get("d61_90", ZERO) + arrears.buckets.get("d90", ZERO)
    repairs = list(MaintenanceRequest.objects.filter(
        organization=account.organization, property__in=props, status__in=MaintenanceRequest.UNFINISHED)
        .select_related("property", "unit").order_by("due_at")[:REPAIRS_SHOWN])
    for pk, count in _repair_counts(account, props).items():
        rows[pk].open_repairs = count
    horizon = today + datetime.timedelta(days=EXPIRING_WITHIN_DAYS)
    ending = list(Lease.objects.filter(unit__property__in=props, status=Lease.Status.ACTIVE,
                                       end_date__gte=today, end_date__lte=horizon)
                  .select_related("unit__property").order_by("end_date"))
    for lease in ending:
        rows[lease.unit.property_id].ending += 1
    ordered = list(rows.values())
    return {
        "rows": ordered, "repairs": repairs, "ending": ending, "horizon_days": EXPIRING_WITHIN_DAYS,
        "units": sum(r.units for r in ordered), "let": sum(r.let for r in ordered),
        "owed": sum((r.owed for r in ordered), ZERO), "over_60": sum((r.over_60 for r in ordered), ZERO),
        "owing_leases": sum(r.owing_leases for r in ordered),
    }


def _repair_counts(account, props) -> dict:
    found = (MaintenanceRequest.objects.filter(organization=account.organization, property__in=props,
                                               status__in=MaintenanceRequest.UNFINISHED)
             .values("property_id").annotate(n=Count("pk")))
    return {r["property_id"]: r["n"] for r in found}
