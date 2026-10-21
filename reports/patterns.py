"""How rent gets paid (D-114): in time or late, month by month; the payment methods over the months; and the
same for one lease.

Nothing new is stored. The figures come from the invoices and payments the dashboard (reports.metrics) and
the unit-by-unit page (reports.units) read, with the same rules: a month's rent is "paid in time" when its
invoice was paid in full by the day it became overdue.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import DateField, Q, Sum
from django.db.models.functions import Cast, TruncMonth
from django.utils.translation import gettext_lazy as _

from core import charts
from core.money import ZERO
from payments.models import Payment

from . import metrics, units
from .metrics import add_months, month_end, month_start, rate
from .units import DUE, LATE, PAID, PART, UNPAID, Cell

# Good to bad, then "not due yet" in grey: the order of the bars, the legend and the table.
STATES = (PAID, LATE, PART, UNPAID, DUE)
COLORS = {PAID: "1", LATE: "late-1", PART: "late-2", UNPAID: "late-4", DUE: "neutral"}


@dataclass
class MonthStates:
    """How many rents billed for one month were paid in time, late, in part, not at all, or are not due yet."""

    month: datetime.date
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(STATES, 0))

    @property
    def label(self) -> str:
        return f"{self.month:%b %Y}"

    @property
    def billed(self) -> int:
        return sum(self.counts.values())

    @property
    def in_time_share(self) -> Decimal | None:
        """Rents paid in time as a share of the rents now due (those not yet due are left out)."""
        return rate(self.counts[PAID], self.billed - self.counts[DUE])


def timeliness(sc: metrics.Scope, last: datetime.date, today: datetime.date,
               count: int = metrics.TREND_MONTHS) -> list[MonthStates]:
    """Each of the `count` months to `last`: every unit's rent for the month, counted by how it was paid."""
    last = month_start(last)
    first = add_months(last, 1 - count)
    rows = {add_months(first, i): MonthStates(add_months(first, i)) for i in range(count)}
    cells = units.rent_states(sc.organization, Q(lease__unit__property__in=sc.properties()), "lease__unit_id",
                              first, last, today)
    for by_month in cells.values():
        for month, cell in by_month.items():
            if cell.state in rows[month].counts:
                rows[month].counts[cell.state] += 1
    return list(rows.values())


def timeliness_from_cells(months: list[datetime.date], cell_rows) -> list[MonthStates]:
    """The same counts from cells already read, such as the unit-by-unit page's rows (one list of cells each)."""
    rows = {m: MonthStates(m) for m in months}
    for cells in cell_rows:
        for cell in cells:
            if cell.month in rows and cell.state in rows[cell.month].counts:
                rows[cell.month].counts[cell.state] += 1
    return list(rows.values())


def timeliness_chart(rows: list[MonthStates]) -> dict:
    return charts.chart("bar", [r.label for r in rows],
                        [charts.series(units.STATE_LABELS[s], [r.counts[s] for r in rows], COLORS[s]) for s in STATES],
                        axis="count", share=True, total=_("Rents billed"))


@dataclass
class MethodMonths:
    """Cash received each month, split by payment method; methods ordered by their total, largest first."""

    months: list[datetime.date]
    methods: list[tuple[str, str]]  # (method, label)
    amounts: dict[tuple[str, datetime.date], Decimal]

    def row(self, method: str) -> list[Decimal]:
        return [self.amounts.get((method, m), ZERO) for m in self.months]

    @property
    def table(self) -> list[tuple[datetime.date, list[Decimal]]]:
        """(month, amount for each method in `methods`) rows, for the table under the chart."""
        return [(m, [self.amounts.get((method, m), ZERO) for method, _label in self.methods]) for m in self.months]


def methods_by_month(sc: metrics.Scope, last: datetime.date, count: int = metrics.TREND_MONTHS) -> MethodMonths:
    last = month_start(last)
    first = add_months(last, 1 - count)
    labels = dict(Payment.Method.choices)
    amounts: dict[tuple[str, datetime.date], Decimal] = {}
    totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for r in (metrics._confirmed_payments(sc, first, month_end(last))
              .annotate(month=Cast(TruncMonth("paid_at"), DateField())).values("method", "month").order_by()
              .annotate(total=Sum("amount"))):
        amounts[r["method"], r["month"]] = r["total"]
        totals[r["method"]] += r["total"]
    methods = sorted(totals, key=lambda m: (-totals[m], m))
    return MethodMonths(months=[add_months(first, i) for i in range(count)],
                        methods=[(m, str(labels.get(m, m))) for m in methods], amounts=amounts)


def methods_chart(mm: MethodMonths, currency: str) -> dict:
    return charts.chart("bar", [f"{m:%b %Y}" for m in mm.months],
                        [charts.series(label, mm.row(method), charts.SERIES[i] if i < len(charts.SERIES) else "neutral")
                         for i, (method, label) in enumerate(mm.methods)],
                        stacked=True, currency=currency, total=_("Cash received"))


def kinds_chart(by_type: list[units.TypeRow]) -> dict:
    """Each kind of unit: how full it is and how much of its rent came in, both as percents of one axis."""
    pct = lambda v: None if v is None else round(float(v) * 100, 1)  # noqa: E731
    return charts.chart("bar", [t.label for t in by_type], [
        charts.series(_("Occupied"), [pct(t.occupancy) for t in by_type], "1"),
        charts.series(_("Rent collected"), [pct(t.rate) for t in by_type], "2"),
    ], axis="pct", horizontal=True)


def pay_days_chart(pay_days: list[units.PayDay], currency: str) -> dict:
    return charts.chart("bar", [p.label for p in pay_days],
                        [charts.series(_("Amount paid"), [p.amount for p in pay_days], "3")], currency=currency)


# ---------------------------------------------------------------------------
# One lease
# ---------------------------------------------------------------------------


@dataclass
class LeaseMonths:
    cells: list[Cell]

    @property
    def due(self) -> list[Cell]:
        """Months with rent billed and now due."""
        return [c for c in self.cells if c.state in (PAID, LATE, PART, UNPAID)]

    @property
    def in_time(self) -> int:
        return sum(1 for c in self.cells if c.state == PAID)

    @property
    def in_time_share(self) -> Decimal | None:
        return rate(self.in_time, len(self.due))

    @property
    def expected(self) -> Decimal:
        return sum((c.expected for c in self.cells), ZERO)

    @property
    def collected(self) -> Decimal:
        return sum((c.collected for c in self.cells), ZERO)

    @property
    def rate(self) -> Decimal | None:
        return rate(self.collected, self.expected)

    @property
    def billed(self) -> bool:
        return any(c.expected for c in self.cells)


def lease_months(lease, today: datetime.date, count: int = metrics.TREND_MONTHS) -> LeaseMonths:
    """One lease's rent for each of the last `count` months: billed, paid, and paid in time or not."""
    last = month_start(today)
    first = add_months(last, 1 - count)
    cells = units.rent_states(lease.organization, Q(lease=lease), "lease_id", first, last, today).get(lease.pk, {})
    return LeaseMonths([cells.get(add_months(first, i), Cell(add_months(first, i))) for i in range(count)])


def lease_chart(lm: LeaseMonths, currency: str) -> dict:
    """Rent billed against paid each month; the paid bar takes the colour of how it was paid, so the page shows
    its own key for those colours."""
    return charts.chart("bar", [f"{c.month:%b %Y}" for c in lm.cells], [
        charts.series(_("Rent billed"), [c.expected for c in lm.cells], "1-soft"),
        charts.series(_("Rent paid"), [c.collected for c in lm.cells], "1",
                      colors=[COLORS.get(c.state, "1") for c in lm.cells]),
    ], currency=currency, legend=False)
