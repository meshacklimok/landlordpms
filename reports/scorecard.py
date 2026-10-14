"""One row per property (D-135): how full, how well it collects, what it earns and what it costs.

Money comes from the profit and loss (reports.finance), so it is on the same cash basis; occupancy is counted
on the period's last day (or today); turnover is move-outs over leases in force, as on the leases report.
Properties are ranked by net operating income per unit.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, Q
from django.utils import timezone

from accounts.permissions import require
from core.money import ZERO, round_money
from leases.models import Lease
from properties.models import Property

from . import finance, metrics
from .metrics import month_start, rate


@dataclass
class Score:
    property: Property | None
    units: int = 0
    occupied: int = 0
    income: Decimal = ZERO
    expenses: Decimal = ZERO
    collection: Decimal | None = None
    move_outs: int = 0
    in_force: int = 0

    @property
    def noi(self) -> Decimal:
        return self.income - self.expenses

    @property
    def occupancy(self) -> Decimal | None:
        return rate(self.occupied, self.units)

    @property
    def noi_per_unit(self) -> Decimal | None:
        return round_money(self.noi / self.units) if self.units else None

    @property
    def income_per_unit(self) -> Decimal | None:
        return round_money(self.income / self.units) if self.units else None

    @property
    def expense_ratio(self) -> Decimal | None:
        return rate(self.expenses, self.income)

    @property
    def turnover(self) -> Decimal | None:
        return rate(self.move_outs, self.in_force)


@dataclass
class Scorecard:
    start: datetime.date
    end: datetime.date
    rows: list[Score]
    total: Score
    currency: str
    generated_at: datetime.datetime


def scorecard(membership, start: datetime.date, end: datetime.date, prop: Property | None = None, *,
              today: datetime.date | None = None, now: datetime.datetime | None = None) -> Scorecard:
    require(membership, "reports.view_financial")
    today = today or timezone.localdate()
    sc = metrics.scope(membership, prop)
    money = finance.money_report(membership, month_start(start), month_start(end), prop)
    figures = {m.property.pk: m.figures for m in money.by_property}
    properties = {p.pk: p for p in sc.properties(include_archived=False)}
    if prop is not None and not money.by_property and prop.pk in properties:
        figures[prop.pk] = money.total
    elif prop is None and not money.by_property and (money.total.income or money.total.expenses):
        # The money page lists properties only when two or more had activity; find the one that did.
        for p in properties.values():
            one = finance.money_report(membership, month_start(start), month_start(end), p).total
            if one.income or one.expenses:
                figures[p.pk] = one

    rows = {pk: Score(p) for pk, p in properties.items()}
    by_key = {p.public_id: pk for pk, p in properties.items()}
    for o in metrics.occupancy_by_property(sc, min(end, today)):
        pk = by_key.get(o.key)
        if pk is not None:
            rows[pk].units, rows[pk].occupied = o.rentable, o.occupied
    for pk, f in figures.items():
        if pk in rows:
            rows[pk].income, rows[pk].expenses, rows[pk].collection = f.income, f.expenses, f.collection_rate

    S = Lease.Status
    base = (Lease.objects.for_org(membership.organization).exclude(status=S.DRAFT)
            .filter(unit__property__in=properties))
    for r in (base.filter(start_date__lte=end)
              .filter(Q(ended_on__gte=start) | Q(ended_on__isnull=True, end_date__gte=start)
                      | Q(ended_on__isnull=True, end_date__isnull=True))
              .values("unit__property_id").annotate(n=Count("pk"))):
        rows[r["unit__property_id"]].in_force = r["n"]
    for r in (base.filter(status__in=(S.ENDED, S.TERMINATED), ended_on__gte=start, ended_on__lte=end)
              .values("unit__property_id").annotate(n=Count("pk"))):
        rows[r["unit__property_id"]].move_outs = r["n"]

    shown = [r for r in rows.values() if r.units or r.income or r.expenses]
    shown.sort(key=lambda r: (r.noi_per_unit is None, -(r.noi_per_unit or ZERO), r.property.name.lower()))
    total = Score(None, collection=money.total.collection_rate)
    for r in shown:
        total.units += r.units
        total.occupied += r.occupied
        total.income += r.income
        total.expenses += r.expenses
        total.move_outs += r.move_outs
        total.in_force += r.in_force
    return Scorecard(start=start, end=end, rows=shown, total=total, currency=membership.organization.currency,
                     generated_at=now or timezone.now())
