"""Deposits held, taken and returned (D-135), from the deposit sub-ledger (billing.DepositEntry).

A deposit belongs to the tenant, so none of this is income. Held is the sum of every entry to date on leases at
the properties in view. Movements are the entries dated in the period. "To settle" is money still held on a
lease that has ended: the landlord owes the tenant a refund or a statement of deductions.
"""

import datetime
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import DateField, Sum
from django.db.models.functions import Cast, TruncMonth
from django.utils import timezone

from accounts.permissions import can, require, visible_properties
from billing.deposits import settle_by
from billing.models import DepositEntry
from core.money import ZERO
from leases.models import Lease
from properties.models import Property

from .metrics import add_months, month_start, rate

LIST_LIMIT = 50
ENDING_WITHIN = 90

Kind = DepositEntry.Kind


@dataclass
class HeldLease:
    lease: object
    held: Decimal
    days: int  # since the lease ended (to settle), or until it ends (ending soon)
    tenant: object = None
    due_on: datetime.date | None = None  # the owner's deadline to refund or account for it (D-150)

    @property
    def overdue(self) -> bool:
        return self.due_on is not None and self.days > (self.due_on - self.lease.ended_on).days


@dataclass
class DepositReport:
    start: datetime.date
    end: datetime.date
    enabled: bool
    held: Decimal = ZERO
    holding: int = 0  # leases holding money
    by_type: list[tuple[str, Decimal]] = field(default_factory=list)
    received: Decimal = ZERO
    deducted: Decimal = ZERO
    refunded: Decimal = ZERO
    corrections: Decimal = ZERO
    months: list[tuple[str, Decimal, Decimal]] = field(default_factory=list)  # label, taken, paid out
    to_settle: list[HeldLease] = field(default_factory=list)
    to_settle_total: Decimal = ZERO
    to_settle_count: int = 0
    ending: list[HeldLease] = field(default_factory=list)
    show_tenants: bool = False
    generated_at: datetime.datetime | None = None

    @property
    def deduction_share(self) -> Decimal | None:
        """Of what left deposits in the period (deducted plus refunded), the share the landlord kept."""
        return rate(self.deducted, self.deducted + self.refunded)


def deposits(membership, start: datetime.date, end: datetime.date, prop: Property | None = None, *,
             today: datetime.date | None = None, now: datetime.datetime | None = None) -> DepositReport:
    require(membership, "reports.view_financial")
    org = membership.organization
    now = now or timezone.now()
    if not org.takes_deposits:
        return DepositReport(start, end, False, generated_at=now)
    today = today or timezone.localdate()
    properties = visible_properties(membership, Property.all_objects.all())
    if prop is not None:
        properties = properties.filter(pk=prop.pk)
    entries = DepositEntry.objects.filter(organization=org, lease__unit__property__in=properties)
    report = DepositReport(start, end, True, show_tenants=can(membership, "tenants.view"), generated_at=now)

    labels = dict(DepositEntry.DepositType.choices)
    for r in entries.values("deposit_type").annotate(s=Sum("amount")).order_by("deposit_type"):
        if r["s"]:
            report.by_type.append((str(labels.get(r["deposit_type"], r["deposit_type"])), r["s"]))
    per_lease = {r["lease_id"]: r["s"] for r in entries.values("lease_id").annotate(s=Sum("amount"))
                 if r["s"] and r["s"] > 0}
    report.held, report.holding = sum(per_lease.values(), ZERO), len(per_lease)

    moved = entries.filter(entry_date__gte=start, entry_date__lte=end)
    for r in moved.values("kind").annotate(s=Sum("amount")).order_by():
        if r["kind"] == Kind.RECEIVED:
            report.received = r["s"]
        elif r["kind"] == Kind.DEDUCTION:
            report.deducted = -r["s"]
        elif r["kind"] == Kind.REFUNDED:
            report.refunded = -r["s"]
        elif r["kind"] == Kind.REVERSAL:
            report.corrections = r["s"]
    taken: dict[datetime.date, list[Decimal]] = {}
    key = month_start(start)
    while key <= month_start(end):
        taken[key] = [ZERO, ZERO]
        key = add_months(key, 1)
    for r in (moved.filter(kind__in=(Kind.RECEIVED, Kind.DEDUCTION, Kind.REFUNDED))
              .annotate(m=Cast(TruncMonth("entry_date"), DateField())).values("m", "kind")
              .annotate(s=Sum("amount")).order_by()):
        if r["kind"] == Kind.RECEIVED:
            taken[r["m"]][0] += r["s"]
        else:
            taken[r["m"]][1] -= r["s"]
    report.months = [(f"{m:%b %Y}", a, b) for m, (a, b) in taken.items()]

    S = Lease.Status
    leases = Lease.all_objects.filter(pk__in=per_lease).select_related("unit__property")
    if report.show_tenants:
        leases = leases.prefetch_related("lease_tenants__tenant")
    settle, ending = [], []
    for lease in leases:
        tenant = lease.primary_tenant if report.show_tenants else None
        if lease.status in (S.ENDED, S.TERMINATED):
            settle.append(HeldLease(lease, per_lease[lease.pk], (today - (lease.ended_on or today)).days, tenant,
                                    settle_by(lease)))
        elif lease.status == S.ACTIVE:
            on = lease.move_out_by or lease.end_date
            if on is not None and today <= on <= today + datetime.timedelta(days=ENDING_WITHIN):
                ending.append(HeldLease(lease, per_lease[lease.pk], (on - today).days, tenant))
    settle.sort(key=lambda h: -h.days)
    ending.sort(key=lambda h: h.days)
    report.to_settle_count = len(settle)
    report.to_settle_total = sum((h.held for h in settle), ZERO)
    report.to_settle, report.ending = settle[:LIST_LIMIT], ending[:LIST_LIMIT]
    return report
