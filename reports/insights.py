"""What the dashboard's figures say, in sentences (D-113): what changed, what is going well, what needs attention.

Nothing new is counted here. Every sentence reads a figure the page already shows, so the two always agree.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from core.money import format_money

from .metrics import Dashboard, rate

GOOD, BAD, NOTE = "good", "bad", "note"
# A change smaller than this (in points or as a share) is not worth a sentence.
SMALL = Decimal("0.02")


@dataclass
class Highlight:
    tone: str  # GOOD, BAD or NOTE: the icon and its colour; the words say the same
    text: str

    @property
    def icon(self) -> str:
        return {GOOD: "arrow-up-right-circle", BAD: "exclamation-circle", NOTE: "info-circle"}[self.tone]


def _pct(value) -> str:
    return f"{value * 100:.0f}%"


def _pts(value) -> str:
    return _("%(n)s pts") % {"n": f"{abs(value) * 100:.1f}"}


def highlights(board: Dashboard, money=None) -> list[Highlight]:
    """Up to eight sentences about the month, most important first. `money` is the 12-month finance report, or None
    when the member cannot see profit and loss."""
    cur = board.currency
    c = board.current
    out: list[Highlight] = []

    rate_change = board.changes["rate"][0]
    if c.rate is not None:
        if rate_change.delta is not None and abs(rate_change.delta) >= SMALL:
            up = rate_change.delta > 0
            out.append(Highlight(GOOD if up else BAD, (
                _("%(rate)s of %(month)s's rent is collected, up %(pts)s on %(before)s.") if up else
                _("%(rate)s of %(month)s's rent is collected, down %(pts)s on %(before)s.")) % {
                    "rate": _pct(c.rate), "month": c.label, "pts": _pts(rate_change.delta),
                    "before": rate_change.label}))
        else:
            out.append(Highlight(GOOD if c.rate >= Decimal("0.9") else NOTE, _(
                "%(rate)s of %(month)s's rent is collected: %(left)s still to collect.") % {
                    "rate": _pct(c.rate), "month": c.label, "left": format_money(c.outstanding, cur)}))

    year = board.changes["collected"][1]
    if year.ratio is not None and abs(year.ratio) >= SMALL:
        up = year.ratio > 0
        out.append(Highlight(GOOD if up else BAD, (
            _("Rent collected is %(r)s higher than in %(before)s.") if up else
            _("Rent collected is %(r)s lower than in %(before)s.")) % {"r": _pct(abs(year.ratio)),
                                                                      "before": year.label}))

    if len(board.collection) > 1 and board.collection[0].rate is not None and board.collection[0].rate < 1:
        worst = board.collection[0]
        out.append(Highlight(BAD if worst.rate < Decimal("0.7") else NOTE, _(
            "%(place)s has collected the least: %(rate)s of its rent, %(left)s still to collect.") % {
                "place": worst.label, "rate": _pct(worst.rate), "left": format_money(worst.outstanding, cur)}))

    total = board.arrears.total
    if total:
        over_90 = next((amount for key, _label, amount in board.arrears.buckets if key == "d90"), 0)
        if over_90:
            out.append(Highlight(BAD, _(
                "%(amount)s has been overdue for more than 90 days: %(share)s of all arrears.") % {
                    "amount": format_money(over_90, cur), "share": _pct(rate(over_90, total))}))
        if board.debtors and board.debtors_share is not None and len(board.debtors) < board.arrears.leases:
            out.append(Highlight(NOTE, ngettext(
                "The largest debt is %(share)s of all arrears.",
                "The %(n)s largest debts are %(share)s of all arrears.", len(board.debtors)) % {
                    "n": len(board.debtors), "share": _pct(board.debtors_share)}))
    elif board.is_current_month:
        out.append(Highlight(GOOD, _("Nothing is overdue today.")))

    if board.vacancy.units and board.vacancy.amount:
        out.append(Highlight(BAD, ngettext(
            "%(n)s unit is empty: %(amount)s a month in asking rent not billed.",
            "%(n)s units are empty: %(amount)s a month in asking rent not billed.", board.vacancy.units) % {
                "n": board.vacancy.units, "amount": format_money(board.vacancy.amount, cur)}))
    elif board.occupancy.rentable and not board.occupancy.vacant:
        out.append(Highlight(GOOD, _("Every rentable unit is let.")))

    if money is not None and money.total.income:
        t = money.total
        out.append(Highlight(GOOD if t.noi > 0 else BAD, _(
            "Over %(period)s: %(income)s came in, %(expenses)s went on expenses, leaving %(noi)s (%(margin)s).") % {
                "period": money.label, "income": format_money(t.income, cur),
                "expenses": format_money(t.expenses, cur), "noi": format_money(t.noi, cur),
                "margin": _pct(t.margin)}))
        if money.expense_categories:
            name, amount = money.expense_categories[0]
            out.append(Highlight(NOTE, _("The largest expense was %(name)s: %(amount)s, %(share)s of expenses.") % {
                "name": name, "amount": format_money(amount, cur), "share": _pct(rate(amount, t.expenses))}))

    if board.unmatched is not None and board.unmatched.count:
        out.append(Highlight(NOTE, ngettext(
            "%(n)s M-Pesa payment (%(amount)s) is waiting to be matched to a tenant.",
            "%(n)s M-Pesa payments (%(amount)s) are waiting to be matched to tenants.", board.unmatched.count) % {
                "n": board.unmatched.count, "amount": format_money(board.unmatched.amount, cur)}))
    return out[:8]
