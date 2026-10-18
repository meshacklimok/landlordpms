"""How each property performs (D-163): what its rent could have been, where it went, what is left, and what it returns.

Two bases, kept apart and labelled on the page, so every figure ties back to a report that already exists:

- The rent bridge is by the month the rent is for. Potential rent is rent billed plus the asking rent of the units
  that stood empty; less what tenants have not paid, it leaves rent collected for those months. Billed and collected
  are the dashboard's (metrics._rent). A unit counts as empty for a month when it is empty on the month's last day
  (today, for this month), and only from the month it was added. Empty units with no asking rent are counted but
  cannot be priced; the page says how many.
- Income, expenses and net operating income are cash, from the profit and loss (finance.money_report) through the
  scorecard. Yield annualises them over the days of the period that have passed, against the estimated value the
  owner gave the property (Property.market_value); a portfolio's yield counts only the properties with a value.

Nothing is stored. Whole months, as on the scorecard and the profit and loss.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from accounts.permissions import require
from core.money import ZERO, format_money, round_money
from leases.services import occupying_leases
from properties.models import Property, Unit

from . import finance, metrics, scorecard
from .insights import BAD, GOOD, NOTE, SMALL, Highlight
from .metrics import add_months, month_end, month_start, rate

YEAR_DAYS = Decimal(365)


@dataclass
class Bridge:
    """Rent for the months of the period, from what it could have been to what was collected."""

    billed: Decimal = ZERO
    collected: Decimal = ZERO
    vacancy: Decimal = ZERO
    empty_months: int = 0  # unit-months empty on the month's last day
    unpriced: int = 0  # of those, with no asking rent, so not in `vacancy`

    @property
    def potential(self) -> Decimal:
        return self.billed + self.vacancy

    @property
    def uncollected(self) -> Decimal:
        return max(self.billed - self.collected, ZERO)

    @property
    def economic_occupancy(self) -> Decimal | None:
        """Rent collected ÷ potential rent: the share of what the property could earn that it did."""
        return rate(self.collected, self.potential)

    @property
    def vacancy_share(self) -> Decimal | None:
        return rate(self.vacancy, self.potential)

    @property
    def uncollected_share(self) -> Decimal | None:
        return rate(self.uncollected, self.potential)

    def add(self, other: "Bridge") -> None:
        for name in ("billed", "collected", "vacancy", "empty_months", "unpriced"):
            setattr(self, name, getattr(self, name) + getattr(other, name))


@dataclass
class Row:
    """One property, or the whole portfolio when `property` is None."""

    property: Property | None
    score: scorecard.Score
    bridge: Bridge
    days: int
    value: Decimal | None = None
    # The cash figures of the properties with a value, which is what yield is worked out on.
    valued_income: Decimal = ZERO
    valued_noi: Decimal = ZERO
    unvalued: int = 0

    @property
    def name(self) -> str:
        return self.property.name if self.property else _("All properties")

    @property
    def months(self) -> Decimal:
        return Decimal(self.days) * 12 / YEAR_DAYS

    def annual(self, amount: Decimal) -> Decimal | None:
        return round_money(amount * YEAR_DAYS / self.days) if self.days else None

    @property
    def noi_per_unit_month(self) -> Decimal | None:
        if not self.score.units or not self.days:
            return None
        return round_money(self.score.noi / self.score.units / self.months)

    @property
    def income_per_unit_month(self) -> Decimal | None:
        if not self.score.units or not self.days:
            return None
        return round_money(self.score.income / self.score.units / self.months)

    @property
    def margin(self) -> Decimal | None:
        return rate(self.score.noi, self.score.income)

    @property
    def gross_yield(self) -> Decimal | None:
        """A year's income ÷ estimated value."""
        if not self.value or not self.days:
            return None
        return rate(self.annual(self.valued_income), self.value)

    @property
    def net_yield(self) -> Decimal | None:
        """A year's net operating income ÷ estimated value: the return on the property before loans and tax."""
        if not self.value or not self.days:
            return None
        return rate(self.annual(self.valued_noi), self.value)


@dataclass
class Benchmark:
    label: str
    this: Decimal | None
    portfolio: Decimal | None
    kind: str = "pct"  # "pct" or "money"
    higher_is_better: bool = True

    @property
    def delta(self) -> Decimal | None:
        if self.this is None or self.portfolio is None:
            return None
        return self.this - self.portfolio

    @property
    def better(self) -> bool | None:
        delta = self.delta
        if delta is None or delta == 0:
            return None
        return (delta > 0) == self.higher_is_better


@dataclass
class Performance:
    start: datetime.date
    end: datetime.date
    today: datetime.date
    currency: str
    rows: list[Row]
    total: Row
    focus: Row
    selected: Property | None
    money: finance.Report
    rent_months: list[metrics.MonthFigures]
    occupancy_months: list[metrics.Occupancy]
    benchmarks: list[Benchmark] = field(default_factory=list)
    rank: int | None = None
    generated_at: datetime.datetime | None = None

    @property
    def months_shown(self) -> list[finance.Month]:
        """The profit and loss months up to this one; months still to come have nothing to show."""
        now = month_start(self.today)
        return [m for m in self.money.months if m.key is None or m.key <= now]

    @property
    def label(self) -> str:
        return self.money.label


def _months(first: datetime.date, last: datetime.date) -> list[datetime.date]:
    out, m = [], first
    while m <= last:
        out.append(m)
        m = add_months(m, 1)
    return out


def _vacancy(sc: metrics.Scope, first: datetime.date, last: datetime.date, today: datetime.date) -> dict[int, Bridge]:
    """Empty units and their asking rent, by property, over the months first..last (none after today)."""
    out: dict[int, Bridge] = defaultdict(Bridge)
    for m in _months(first, last):
        day = min(today, month_end(m))
        if day < m:
            break
        rows = (Unit.objects.filter(organization=sc.organization, property__in=sc.properties(include_archived=False),
                                    created_at__date__lte=day)
                .exclude(manual_status=Unit.ManualStatus.INACTIVE)
                .exclude(pk__in=occupying_leases(day).values("unit_id"))
                .values("property_id").order_by()
                .annotate(n=Count("pk"), rent=Sum("list_rent"), unpriced=Count("pk", filter=Q(list_rent__isnull=True))))
        for r in rows:
            b = out[r["property_id"]]
            b.vacancy += r["rent"] or ZERO
            b.empty_months += r["n"]
            b.unpriced += r["unpriced"]
    return out


def performance(membership, start: datetime.date, end: datetime.date, prop: Property | None = None, *,
                today: datetime.date | None = None, now: datetime.datetime | None = None) -> Performance:
    require(membership, "reports.view_financial")
    today = today or timezone.localdate()
    first, last = month_start(start), month_start(end)
    # The days of the period that have passed, which a year's figures are scaled from.
    days = max((min(today, month_end(last)) - first).days + 1, 0)

    card = scorecard.scorecard(membership, start, end, today=today, now=now)
    sc = metrics.scope(membership, capability="reports.view_financial")
    bridges: dict[int, Bridge] = defaultdict(Bridge)
    for (pk, _month), (expected, collected) in metrics._rent(sc, first, last).items():
        bridges[pk].billed += expected
        bridges[pk].collected += collected
    for pk, b in _vacancy(sc, first, last, today).items():
        bridges[pk].vacancy += b.vacancy
        bridges[pk].empty_months += b.empty_months
        bridges[pk].unpriced += b.unpriced

    rows = []
    for s in card.rows:
        value = s.property.market_value or None
        rows.append(Row(property=s.property, score=s, bridge=bridges.get(s.property.pk, Bridge()), days=days,
                        value=value, valued_income=s.income if value else ZERO, valued_noi=s.noi if value else ZERO,
                        unvalued=0 if value else 1))
    total = Row(property=None, score=card.total, bridge=Bridge(), days=days)
    for r in rows:
        total.bridge.add(r.bridge)
        total.valued_income += r.valued_income
        total.valued_noi += r.valued_noi
        total.unvalued += r.unvalued
        if r.value:
            total.value = (total.value or ZERO) + r.value

    focus, rank = total, None
    if prop is not None:
        found = [i for i, r in enumerate(rows) if r.property.pk == prop.pk]
        if found:
            focus, rank = rows[found[0]], found[0] + 1
        else:
            focus = Row(property=prop, score=scorecard.Score(prop), bridge=Bridge(), days=days,
                        value=prop.market_value or None, unvalued=0 if prop.market_value else 1)

    focus_scope = metrics.scope(membership, prop, capability="reports.view_financial")
    shown_last = min(last, month_start(today))
    report = Performance(
        start=first, end=last, today=today, currency=membership.organization.currency, rows=rows, total=total,
        focus=focus, selected=prop, money=finance.money_report(membership, first, last, prop, compare=True),
        rent_months=metrics.months(focus_scope, first, shown_last) if shown_last >= first else [],
        occupancy_months=(metrics.occupancy_trend(focus_scope, shown_last, today, count=len(_months(first, shown_last)))
                          if shown_last >= first else []),
        rank=rank, generated_at=now or timezone.now())
    if prop is not None and len(rows) > 1:
        report.benchmarks = benchmarks(focus, total)
    return report


def benchmarks(this: Row, portfolio: Row) -> list[Benchmark]:
    """One property's ratios against the whole portfolio's."""
    return [
        Benchmark(_("Occupancy"), this.score.occupancy, portfolio.score.occupancy),
        Benchmark(_("Rent earned of potential"), this.bridge.economic_occupancy, portfolio.bridge.economic_occupancy),
        Benchmark(_("Rent collected of billed"), this.score.collection, portfolio.score.collection),
        Benchmark(_("Lost to empty units"), this.bridge.vacancy_share, portfolio.bridge.vacancy_share,
                  higher_is_better=False),
        Benchmark(_("Expenses of income"), this.score.expense_ratio, portfolio.score.expense_ratio,
                  higher_is_better=False),
        Benchmark(_("Net income per unit a month"), this.noi_per_unit_month, portfolio.noi_per_unit_month, "money"),
        Benchmark(_("Net yield a year"), this.net_yield, portfolio.net_yield),
    ]


def _pct(value) -> str:
    return f"{value * 100:.0f}%"


def _pct1(value) -> str:
    return f"{value * 100:.1f}%"


def highlights(r: Performance, cur: str) -> list[Highlight]:
    """What the page's figures say, most important first. Nothing new is counted (as report_notes)."""
    f, b, out = r.focus, r.focus.bridge, []
    if b.potential:
        earned = b.economic_occupancy
        tone = GOOD if earned >= Decimal("0.9") else NOTE if earned >= Decimal("0.75") else BAD
        out.append(Highlight(tone, _("Of %(potential)s it could have earned in rent, %(collected)s (%(p)s) was "
                                     "collected.") % {"potential": format_money(b.potential, cur),
                                                      "collected": format_money(b.collected, cur),
                                                      "p": _pct(b.economic_occupancy)}))
    if b.vacancy and b.vacancy_share >= SMALL:
        out.append(Highlight(BAD, ngettext(
            "Empty units cost %(amount)s (%(p)s of potential rent) over %(n)s unit-month.",
            "Empty units cost %(amount)s (%(p)s of potential rent) over %(n)s unit-months.", b.empty_months)
            % {"amount": format_money(b.vacancy, cur), "p": _pct(b.vacancy_share), "n": b.empty_months}))
    if b.uncollected and b.uncollected_share >= SMALL:
        out.append(Highlight(BAD, _("%(amount)s of rent billed for these months is still unpaid.")
                             % {"amount": format_money(b.uncollected, cur)}))
    noi = r.money.change("noi")
    if noi.ratio is not None and abs(noi.ratio) >= SMALL:
        out.append(Highlight(GOOD if noi.delta > 0 else BAD,
                             _("Net operating income is %(p)s %(dir)s on %(when)s.")
                             % {"p": _pct(abs(noi.ratio)), "dir": _("up") if noi.delta > 0 else _("down"),
                                "when": noi.label}))
    before = r.money.previous.total.expense_ratio if r.money.previous else None
    now = f.score.expense_ratio
    if now is not None and before is not None and now - before >= Decimal("0.05"):
        out.append(Highlight(BAD, _("Expenses took %(now)s of income, against %(before)s before.")
                             % {"now": _pct(now), "before": _pct(before)}))
    if f.net_yield is not None:
        out.append(Highlight(NOTE, _("Net yield is %(y)s a year on an estimated value of %(v)s%(part)s.")
                             % {"y": _pct1(f.net_yield), "v": format_money(f.value, cur),
                                "part": (ngettext(" (%(n)s property without a value is left out)",
                                                  " (%(n)s properties without a value are left out)", f.unvalued)
                                         % {"n": f.unvalued}) if f.property is None and f.unvalued else ""}))
    elif f.score.income:
        out.append(Highlight(NOTE, _("Give the property an estimated value to see its yield.")
                             if f.property else _("Give your properties an estimated value to see their yield.")))
    if r.rank is not None and len(r.rows) > 1:
        out.append(Highlight(NOTE, _("Ranks %(k)s of %(n)s on net income per unit.") % {"k": r.rank, "n": len(r.rows)}))
    elif r.selected is None and len(r.rows) > 1:
        ranked = [x for x in r.rows if x.noi_per_unit_month is not None]
        if len(ranked) > 1:
            best, worst = ranked[0], ranked[-1]
            out.append(Highlight(NOTE, _("%(best)s earns the most net income per unit (%(a)s a month); "
                                         "%(worst)s the least (%(b)s).")
                                 % {"best": best.name, "a": format_money(best.noi_per_unit_month, cur),
                                    "worst": worst.name, "b": format_money(worst.noi_per_unit_month, cur)}))
    return out
