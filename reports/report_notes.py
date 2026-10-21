"""What each report page says in a few sentences (D-132). Like the dashboard highlights, nothing new is counted:
every sentence reads a figure the page already shows."""

from decimal import Decimal

from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from core.money import format_money

from .insights import BAD, GOOD, NOTE, Highlight


def _pct(value) -> str:
    return f"{value * 100:.0f}%"


def arrears(r, cur) -> list[Highlight]:
    out = []
    if not r.owed:
        return [Highlight(GOOD, _("Nothing is owed."))]
    if r.overdue:
        out.append(Highlight(BAD, ngettext(
            "%(amount)s is overdue on %(n)s lease.", "%(amount)s is overdue across %(n)s leases.", r.leases)
            % {"amount": format_money(r.overdue, cur), "n": r.leases}))
    else:
        out.append(Highlight(GOOD, _("Nothing is overdue; what is owed is not yet due.")))
    if r.over_60_share and r.over_60_share >= Decimal("0.25"):
        out.append(Highlight(BAD, _("%(p)s of what is owed is more than 60 days late.") % {"p": _pct(r.over_60_share)}))
    if r.recurring:
        out.append(Highlight(NOTE, ngettext(
            "%(n)s lease paid late in at least 3 of the last 6 months.",
            "%(n)s leases paid late in at least 3 of the last 6 months.", len(r.recurring)) % {"n": len(r.recurring)}))
    if r.recovered:
        out.append(Highlight(GOOD, _("%(amount)s of late rent was recovered in the period.")
                             % {"amount": format_money(r.recovered, cur)}))
    return out


def payments(r, cur) -> list[Highlight]:
    out = []
    if r.confirmed:
        out.append(Highlight(NOTE, ngettext("%(amount)s received in %(n)s payment.",
                                            "%(amount)s received in %(n)s payments.", r.confirmed_count)
                             % {"amount": format_money(r.confirmed, cur), "n": r.confirmed_count}))
    if r.methods:
        label, _n, _a, share = r.methods[0]
        if share is not None and len(r.methods) > 1:
            out.append(Highlight(NOTE, _("%(m)s was the most used way to pay: %(p)s of the money.")
                                 % {"m": label, "p": _pct(share)}))
    confirmed = [x for x in getattr(r, "lines", []) if x.state == "confirmed"]
    if len(confirmed) > 1:
        top = max(confirmed, key=lambda x: x.amount)
        out.append(Highlight(NOTE, _("The largest payment was %(amount)s on %(day)s.")
                             % {"amount": format_money(top.amount, cur), "day": f"{top.paid_at:%d %b %Y}"}))
        if r.confirmed_count:
            out.append(Highlight(NOTE, _("The average payment was %(amount)s.")
                                 % {"amount": format_money(r.confirmed / r.confirmed_count, cur)}))
    if r.waiting:
        out.append(Highlight(BAD, _("%(n)s payments are waiting for review (%(amount)s).")
                             % {"n": r.waiting, "amount": format_money(r.waiting_amount, cur)}))
    if r.undone:
        out.append(Highlight(NOTE, _("%(amount)s was reversed or rejected and is not counted.")
                             % {"amount": format_money(r.undone, cur)}))
    return out


def expenses(r, cur) -> list[Highlight]:
    out = []
    if not r.total:
        return out
    out.append(Highlight(NOTE, ngettext("%(amount)s spent in %(n)s expense.", "%(amount)s spent in %(n)s expenses.",
                                        r.count) % {"amount": format_money(r.total, cur), "n": r.count}))
    if r.previous_total:
        change = (r.total - r.previous_total) / r.previous_total
        if abs(change) >= Decimal("0.05"):
            tone = BAD if change > 0 else GOOD
            text = (_("Spending is up %(p)s on the period before.") if change > 0
                    else _("Spending is down %(p)s on the period before.")) % {"p": _pct(abs(change))}
            out.append(Highlight(tone, text))
    if r.by_category and len(r.by_category) > 1:
        label, _n, _a, share = r.by_category[0]
        if share is not None:
            out.append(Highlight(NOTE, _("%(c)s was the largest cost: %(p)s of the spending.")
                                 % {"c": label, "p": _pct(share)}))
    suppliers = [x for x in r.by_supplier if x[0] != "—"]
    if suppliers:
        label, _n, amount, _s = suppliers[0]
        out.append(Highlight(NOTE, _("%(s)s was paid the most: %(amount)s.")
                             % {"s": label, "amount": format_money(amount, cur)}))
    if r.lines:
        top = max(r.lines, key=lambda e: e.amount)
        out.append(Highlight(NOTE, _("The largest single expense was %(amount)s, on %(day)s.")
                             % {"amount": format_money(top.amount, cur), "day": f"{top.paid_on:%d %b %Y}"}))
    if r.waiting:
        out.append(Highlight(BAD, ngettext("%(n)s expense (%(amount)s) is waiting for approval and not counted.",
                                           "%(n)s expenses (%(amount)s) are waiting for approval and not counted.",
                                           r.waiting) % {"n": r.waiting, "amount": format_money(r.waiting_amount, cur)}))
    return out


def maintenance(r, cur) -> list[Highlight]:
    out = []
    if r.overdue_now:
        out.append(Highlight(BAD, ngettext("%(n)s open request is past its fix-by time.",
                                           "%(n)s open requests are past their fix-by time.", r.overdue_now)
                             % {"n": r.overdue_now}))
    if r.fixed_in_time_share is not None:
        out.append(Highlight(GOOD if r.fixed_in_time_share >= Decimal("0.8") else NOTE,
                             _("%(p)s of the repairs were finished on time.") % {"p": _pct(r.fixed_in_time_share)}))
    if r.repeats:
        out.append(Highlight(NOTE, ngettext("%(n)s problem came back three or more times.",
                                            "%(n)s problems came back three or more times.", len(r.repeats))
                             % {"n": len(r.repeats)}))
    if r.cost:
        out.append(Highlight(NOTE, _("Repairs cost %(amount)s.") % {"amount": format_money(r.cost, cur)}))
    return out


def leases(r, cur) -> list[Highlight]:
    out = []
    if r.turnover is not None and r.move_outs:
        out.append(Highlight(NOTE, _("%(n)s moved out: %(p)s turnover.") % {"n": r.move_outs, "p": _pct(r.turnover)}))
    soon = dict(r.windows).get(30, 0)
    if soon:
        out.append(Highlight(NOTE, ngettext("%(n)s lease ends within 30 days.", "%(n)s leases end within 30 days.",
                                            soon) % {"n": soon}))
    if r.holding_over:
        out.append(Highlight(BAD, ngettext("%(n)s lease is past its end date and still active.",
                                           "%(n)s leases are past their end date and still active.",
                                           len(r.holding_over)) % {"n": len(r.holding_over)}))
    return out


def utilities(r, cur) -> list[Highlight]:
    out = []
    if r.show_money and r.uninvoiced:
        out.append(Highlight(NOTE, _("%(amount)s of meter charges is not on an invoice yet.")
                             % {"amount": format_money(r.uninvoiced, cur)}))
    if r.flagged:
        out.append(Highlight(BAD, ngettext("%(n)s approved reading has a warning flag.",
                                           "%(n)s approved readings have a warning flag.", r.flagged)
                             % {"n": r.flagged}))
    if r.waiting:
        out.append(Highlight(NOTE, ngettext("%(n)s reading is waiting for approval.",
                                            "%(n)s readings are waiting for approval.", r.waiting) % {"n": r.waiting}))
    return out


def delivery(r, cur) -> list[Highlight]:
    out = []
    if not r.total:
        return out
    if r.failure_rate is not None:
        bad = r.failure_rate >= Decimal("0.1")
        out.append(Highlight(BAD if bad else GOOD, _("%(p)s of the %(n)s messages tried failed.")
                             % {"p": _pct(r.failure_rate), "n": r.attempted}))
    worst = max((c for c in r.channels if c.failure_rate is not None and c.attempted >= 5 and len(r.channels) > 1),
                key=lambda c: c.failure_rate, default=None)
    if worst is not None and worst.failure_rate >= Decimal("0.1"):
        out.append(Highlight(BAD, _("%(c)s fails most: %(p)s of its messages.")
                             % {"c": worst.label, "p": _pct(worst.failure_rate)}))
    if r.skipped:
        label, n = r.skipped[0]
        out.append(Highlight(NOTE, _("Most skipped messages were skipped because: %(why)s (%(n)s).")
                             % {"why": label, "n": n}))
    if r.cost:
        out.append(Highlight(NOTE, _("Messages cost %(amount)s.") % {"amount": format_money(r.cost, cur)}))
    return out


NOTES = {"arrears": arrears, "payments": payments, "expenses": expenses, "maintenance": maintenance, "leases": leases,
         "utilities": utilities, "message-delivery": delivery}
