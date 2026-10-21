"""True property profitability (D-191): what each property earns after everything it costs to run.

One page that joins figures the other reports keep apart, each from the same source as its own report:

- Rent: potential, empty units, billed and collected, from the performance report (rent by the month it is for).
- Income: money received, split into rent, water and utilities, and other charges, from the profit and loss (cash).
- Costs: approved expenses grouped by the kind of cost of their category (ExpenseCategory.cost_class), plus the
  management fee worked out on the properties that have one (statements.fee_for, as on the owner statement and the
  fee report). A fee the landlord also records as an expense is counted in both; the page says so.
- Net operating income: income less all of those costs, so it is the profit and loss's figure less the fees.
- Owed: what tenants still owe today, from the aged receivables.

Nothing is stored. Whole months, as on the profit and loss.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import DateField, Sum
from django.db.models.functions import Cast, TruncMonth
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.permissions import require
from core.money import ZERO
from expenses import services as expense_services
from expenses.models import CostClass
from payments.models import Payment
from properties.models import Property

from . import finance, performance
from .income import _invoice_shares, _split
from .metrics import add_months, month_end, month_start, rate
from .statements import fee_for

CLASSES = [value for value, _label in CostClass.choices]
# Costs the fee line stands in for: expenses of this class and the computed fee are shown together.
MANAGEMENT = CostClass.MANAGEMENT.value


@dataclass
class Profit:
    """One property, or all of them when `property` is None."""

    property: Property | None
    units: int = 0
    potential: Decimal = ZERO
    vacancy: Decimal = ZERO
    billed: Decimal = ZERO
    collected: Decimal = ZERO
    rent: Decimal = ZERO  # rent received in cash, with money not yet applied
    utilities: Decimal = ZERO
    other: Decimal = ZERO
    costs: dict = field(default_factory=lambda: dict.fromkeys(CLASSES, ZERO))
    fee: Decimal = ZERO  # management fee worked out from the property's fee settings
    owed: Decimal = ZERO
    overdue: Decimal = ZERO

    @property
    def name(self) -> str:
        return self.property.name if self.property else _("All properties")

    @property
    def uncollected(self) -> Decimal:
        return max(self.billed - self.collected, ZERO)

    @property
    def income(self) -> Decimal:
        return self.rent + self.utilities + self.other

    @property
    def operating_costs(self) -> Decimal:
        """Everything spent on running the property, with the management fee."""
        return sum(self.costs.values(), ZERO) + self.fee

    @property
    def costs_with_fee(self) -> dict:
        return {**self.costs, MANAGEMENT: self.costs[MANAGEMENT] + self.fee}

    @property
    def noi(self) -> Decimal:
        return self.income - self.operating_costs

    @property
    def margin(self) -> Decimal | None:
        return rate(self.noi, self.income)

    @property
    def cost_ratio(self) -> Decimal | None:
        return rate(self.operating_costs, self.income)

    @property
    def vacancy_share(self) -> Decimal | None:
        return rate(self.vacancy, self.potential)

    @property
    def utility_result(self) -> Decimal:
        """Water and utility income less the water and electricity bills paid."""
        return self.utilities - self.costs[CostClass.UTILITIES.value]

    @property
    def noi_per_unit(self) -> Decimal | None:
        return rate(self.noi, self.units) if self.units else None

    def add(self, other: "Profit") -> None:
        for name in ("units", "potential", "vacancy", "billed", "collected", "rent", "utilities", "other", "fee",
                     "owed", "overdue"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        for key in CLASSES:
            self.costs[key] += other.costs[key]


@dataclass
class Profitability:
    start: datetime.date
    end: datetime.date
    currency: str
    rows: list[Profit]  # best net operating income first
    total: Profit
    focus: Profit
    selected: Property | None
    months: list[str]
    # Per month, oldest first, for the chart: cost class -> amounts, then income and net.
    month_costs: dict
    month_income: list
    month_noi: list
    generated_at: datetime.datetime
    fees_counted: bool = False  # some property has a management fee

    @property
    def label(self) -> str:
        return f"{self.start:%b %Y}" if self.start == self.end else f"{self.start:%b %Y} – {self.end:%b %Y}"


def _fees(sc_props, org, first, last, today) -> tuple[dict, dict]:
    """Management fee by property and by month, for the months up to today. Properties without a fee give none."""
    props = [p for p in sc_props if p.management_fee_percent or p.management_fee_fixed]
    by_prop: dict[int, Decimal] = defaultdict(lambda: ZERO)
    by_month: dict[datetime.date, Decimal] = defaultdict(lambda: ZERO)
    if not props:
        return by_prop, by_month
    rent: dict[tuple[int, datetime.date], Decimal] = defaultdict(lambda: ZERO)
    payments = list(Payment.objects.filter(organization=org, status=Payment.Status.CONFIRMED, paid_at__gte=first,
                                           paid_at__lte=month_end(last), lease__unit__property__in=props)
                    .select_related("lease__unit").prefetch_related("allocations"))
    shares = _invoice_shares(payments)
    for p in payments:
        part = _split(p, shares)
        rent[(p.lease.unit.property_id, month_start(p.paid_at))] += part.rent + part.unapplied
    month = first
    while month <= min(last, month_start(today)):
        for prop in props:
            amount = fee_for(prop, rent[(prop.pk, month)], month)
            by_prop[prop.pk] += amount
            by_month[month] += amount
        month = add_months(month, 1)
    return by_prop, by_month


def profitability(membership, start: datetime.date, end: datetime.date, prop: Property | None = None, *,
                  today: datetime.date | None = None, now: datetime.datetime | None = None) -> Profitability:
    require(membership, "reports.view_financial")
    org = membership.organization
    today = today or timezone.localdate()
    first, last = month_start(start), month_start(end)
    perf = performance.performance(membership, first, last, prop, today=today, now=now)
    properties = finance._properties(membership, prop)
    shown = {r.property.pk: r for r in perf.rows}

    # Cash income by property, as the scorecard finds it: the money page lists properties only when two or more
    # had activity.
    figures = {m.property.pk: m.figures for m in perf.money.by_property}
    if not figures and (perf.money.total.income or perf.money.total.expenses):
        if len(shown) == 1:
            figures[next(iter(shown))] = perf.money.total
        else:
            for pk, row in shown.items():
                one = finance.money_report(membership, first, last, row.property).total
                if one.income or one.expenses:
                    figures[pk] = one

    spent = expense_services.approved(org, properties, first, month_end(last))
    by_class: dict[tuple[int, str], Decimal] = defaultdict(lambda: ZERO)
    for r in spent.values("property_id", "category__cost_class").annotate(total=Sum("amount")).order_by():
        by_class[(r["property_id"], r["category__cost_class"])] += r["total"]
    month_class: dict[str, dict[datetime.date, Decimal]] = {key: defaultdict(lambda: ZERO) for key in CLASSES}
    for r in (spent.annotate(bucket=Cast(TruncMonth("paid_on"), DateField()))
              .values("bucket", "category__cost_class").annotate(total=Sum("amount")).order_by()):
        month_class[r["category__cost_class"]][r["bucket"]] += r["total"]

    props = list(properties.filter(pk__in=shown) if shown else properties)
    fee_by_prop, fee_by_month = _fees(props, org, first, last, today)

    owed, overdue = {}, {}
    aging, _total = finance.receivables(membership, today, prop)
    for row in aging:
        owed[row.property.pk] = row.total
        overdue[row.property.pk] = row.total - row.buckets["current"]

    rows = []
    for pk, r in shown.items():
        p = Profit(r.property, units=r.score.units, potential=r.bridge.potential, vacancy=r.bridge.vacancy,
                   billed=r.bridge.billed, collected=r.bridge.collected, fee=fee_by_prop.get(pk, ZERO),
                   owed=owed.get(pk, ZERO), overdue=overdue.get(pk, ZERO))
        money = figures.get(pk)
        if money is not None:
            p.rent, p.utilities, p.other = money.rent + money.unapplied, money.utilities, money.other
        for key in CLASSES:
            p.costs[key] = by_class.get((pk, key), ZERO)
        rows.append(p)
    rows.sort(key=lambda p: (-p.noi, p.name.lower()))

    total = Profit(None)
    for p in rows:
        total.add(p)
    focus = total
    if prop is not None:
        focus = next((p for p in rows if p.property.pk == prop.pk), Profit(prop))

    months = list(perf.months_shown)
    month_costs = {key: [month_class[key].get(m.key, ZERO) + (fee_by_month.get(m.key, ZERO) if key == MANAGEMENT
                                                              else ZERO) for m in months] for key in CLASSES}
    month_income = [m.income for m in months]
    month_noi = [m.income - sum(month_costs[key][i] for key in CLASSES) for i, m in enumerate(months)]
    return Profitability(
        start=first, end=last, currency=org.currency, rows=rows, total=total, focus=focus, selected=prop,
        months=[m.label for m in months], month_costs=month_costs, month_income=month_income, month_noi=month_noi,
        generated_at=perf.generated_at, fees_counted=any(p.management_fee_percent or p.management_fee_fixed
                                                         for p in props))
