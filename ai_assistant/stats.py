"""The assistant as a statistics helper (D-136): the reports' figures, read through the same code the report pages use.

Each tool wraps one report, checks the capability that report's page checks, and returns plain figures already
worked out (shares, changes against the period before, best and worst) so the model explains them and does not
calculate. Nothing here is counted a second time: a number is whatever the report page would show.
"""

import datetime
from decimal import Decimal

from django.urls import reverse

from accounts.models import Membership
from accounts.permissions import can
from reports import behaviour, finance, insights, metrics, operations, scorecard, units
from reports.metrics import add_months, month_end

from .tools import Tool, _money, _property, _text, tenant_names

SHOWN = 8  # rows of a ranking handed to the model


def _p(fraction, places=1) -> str | None:
    return None if fraction is None else f"{Decimal(fraction) * 100:.{places}f}%"


def _change(m: Membership, change: metrics.Change, *, money=True, rate=False) -> dict | None:
    """A figure against the same figure earlier: before, the difference and, for money, the percentage."""
    if change.delta is None:
        return None
    if rate:
        return {"against": change.label, "before": _p(change.before), "points": round(float(change.delta) * 100, 1)}
    show = (lambda v: _money(m, v)) if money else (lambda v: v)
    out = {"against": change.label, "before": show(change.before), "difference": show(change.delta)}
    if change.ratio is not None:
        out["percent"] = _p(change.ratio)
    return out


def _span(args: dict, today: datetime.date) -> tuple[datetime.date, datetime.date]:
    """(first month, last month) from `from` and `to` as YYYY-MM; at most 12 months, default the last 6."""
    return finance.period(_text(args.get("from"), 7), _text(args.get("to"), 7), today)


def _period_name(first: datetime.date, last: datetime.date) -> str:
    return f"{first:%b %Y}" if first == last else f"{first:%b %Y} to {last:%b %Y}"


def _month_figures(m: Membership, f: finance.Month) -> dict:
    return {"month": f.label, "income": _money(m, f.income), "rent_received": _money(m, f.rent),
            "expenses": _money(m, f.expenses), "net_operating_income": _money(m, f.noi),
            "rent_billed": _money(m, f.rent_billed), "collection_rate": _p(f.collection_rate),
            "expense_ratio": _p(f.expense_ratio), "margin": _p(f.margin)}


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------


def month_review(m: Membership, args: dict, today: datetime.date) -> dict:
    """The dashboard page for one month, with its comparisons and its sentences."""
    month = today
    if args.get("month"):
        try:
            month = datetime.date.fromisoformat(_text(args["month"], 10)[:7] + "-01")
        except ValueError:
            return {"error": "give the month as YYYY-MM"}
    prop, error = _property(m, args.get("property"))
    if error:
        return error
    board = metrics.dashboard(m, month=month, property=prop, today=today, analysis=True)
    money_report = None
    if can(m, "reports.view_financial"):
        money_report = finance.money_report(m, add_months(board.month, -11), board.month, prop)
    cur = board.current
    ch = board.changes
    out = {
        "month": cur.label, "property": prop.name if prop else "all this member can see",
        "units": board.occupancy.rentable, "occupied": board.occupancy.occupied,
        "occupancy": _p(board.occupancy.rate),
        "occupancy_vs_last_month": _change(m, board.occupancy_change, money=False),
        "rent_expected": _money(m, cur.expected), "rent_collected": _money(m, cur.collected),
        "collection_rate": _p(cur.rate), "still_to_collect": _money(m, cur.outstanding),
        "cash_received": _money(m, cur.cash),
        "against_last_month": {name: _change(m, ch[name][0], money=name != "rate", rate=name == "rate")
                               for name in ("expected", "collected", "rate", "cash")},
        "against_same_month_last_year": {name: _change(m, ch[name][1], money=name != "rate", rate=name == "rate")
                                         for name in ("expected", "collected", "rate", "cash")},
        "collection_by_property_lowest_first": [
            {"property": r.label, "expected": _money(m, r.expected), "collected": _money(m, r.collected),
             "rate": _p(r.rate), "left": _money(m, r.outstanding)} for r in board.collection[:SHOWN]],
        "cash_by_method": [{"method": r.label, "payments": r.count, "amount": _money(m, r.amount)}
                           for r in board.methods],
        "empty_units": board.vacancy.units, "asking_rent_not_billed_a_month": _money(m, board.vacancy.amount),
        "empty_units_without_an_asking_rent": board.vacancy.unpriced,
        "tenants_by_payment_grade_A_best_E_worst": dict(board.grade_mix),
        "overdue_today_by_age": {str(label): _money(m, amount) for _k, label, amount in board.arrears.buckets},
        "overdue_total": _money(m, board.arrears.total), "leases_overdue": board.arrears.leases,
        "largest_debts_share_of_arrears": _p(board.debtors_share),
        "occupancy_by_month": [{"month": o.label, "occupied": o.occupied, "units": o.rentable,
                                "occupancy": _p(o.rate)} for o in board.occupancy_trend],
        "what_the_page_says": [h.text for h in insights.highlights(board, money_report)],
        "label": "Dashboard", "url": reverse("reports:dashboard"),
    }
    if board.unmatched is not None:
        out["mpesa_waiting_to_match"] = {"payments": board.unmatched.count,
                                         "amount": _money(m, board.unmatched.amount)}
    if board.collected_share is not None:
        out["share_of_cash_that_was_this_months_rent"] = _p(board.collected_share)
    return out


def trend(m: Membership, args: dict, today: datetime.date) -> dict:
    """Month by month rent expected, collected and cash, with the direction over the period."""
    first, last = _span(args, today)
    prop, error = _property(m, args.get("property"))
    if error:
        return error
    sc = metrics.scope(m, prop)
    count = (last.year - first.year) * 12 + last.month - first.month + 1
    rows = metrics.trend(sc, last, count)
    occupancy = metrics.occupancy_trend(sc, last, today, count)
    billed = [r for r in rows if r.expected]
    out = {"period": _period_name(first, last), "property": prop.name if prop else "all this member can see",
           "months": [{"month": r.label, "expected": _money(m, r.expected), "collected": _money(m, r.collected),
                       "rate": _p(r.rate), "cash": _money(m, r.cash), "occupancy": _p(o.rate),
                       "occupied": o.occupied} for r, o in zip(rows, occupancy, strict=True)],
           "label": "Dashboard", "url": reverse("reports:dashboard")}
    if billed:
        best = max(billed, key=lambda r: (r.rate or 0))
        worst = min(billed, key=lambda r: (r.rate or 0))
        out["totals"] = {"expected": _money(m, sum((r.expected for r in billed), 0)),
                         "collected": _money(m, sum((r.collected for r in billed), 0)),
                         "cash": _money(m, sum((r.cash for r in rows), 0)),
                         "average_collection_rate": _p(metrics.rate(sum((r.collected for r in billed), 0),
                                                                    sum((r.expected for r in billed), 0)))}
        out["best_collection_month"] = {"month": best.label, "rate": _p(best.rate)}
        out["worst_collection_month"] = {"month": worst.label, "rate": _p(worst.rate)}
        if len(billed) > 1:
            a, b = billed[0], billed[-1]
            out["first_to_last_month"] = {
                "expected_change": _change(m, metrics.Change(b.expected, a.expected, a.label)),
                "collected_change": _change(m, metrics.Change(b.collected, a.collected, a.label)),
                "rate_change": _change(m, metrics.Change(b.rate, a.rate, a.label), rate=True)
                if a.rate is not None and b.rate is not None else None}
    return out


def money(m: Membership, args: dict, today: datetime.date) -> dict:
    """Profit and loss for a period against the period before: income, expenses, net, margin, where it went."""
    first, last = _span(args, today)
    prop, error = _property(m, args.get("property"))
    if error:
        return error
    report = finance.money_report(m, first, last, prop, compare=True)
    t = report.total
    out = {"period": report.label, "compared_with": report.previous.label if report.previous else None,
           "basis": "cash: counted in the month received or paid; expenses once approved",
           "total": _month_figures(m, t),
           "monthly_average": {"income": _money(m, report.average.income),
                               "expenses": _money(m, report.average.expenses),
                               "net_operating_income": _money(m, report.average.noi)},
           "against_previous_period": {name: _change(m, report.change(name))
                                       for name in ("income", "expenses", "noi")},
           "months": [_month_figures(m, f) for f in report.months],
           "expense_categories": [{"category": name, "amount": _money(m, amount),
                                   "share_of_expenses": _p(metrics.rate(amount, t.expenses))}
                                  for name, amount in report.expense_categories[:SHOWN]],
           "deposits_received_not_income": _money(m, t.deposits),
           "deposit_refunds": _money(m, t.refunds), "paid_to_owners": _money(m, t.owner_payments),
           "net_cash_movement": _money(m, t.net),
           "notes": [str(n) for n in report.notes], "label": "Profit and loss",
           "url": reverse("reports:profit_loss")}
    if report.months:
        billed = [f for f in report.months if f.income or f.expenses]
        if billed:
            best, worst = max(billed, key=lambda f: f.noi), min(billed, key=lambda f: f.noi)
            out["best_month_by_net"] = {"month": best.label, "net": _money(m, best.noi)}
            out["worst_month_by_net"] = {"month": worst.label, "net": _money(m, worst.noi)}
    if report.by_property:
        out["by_property_best_net_first"] = [
            {"property": r.property.name, **{k: v for k, v in _month_figures(m, r.figures).items()
                                              if k != "month"}} for r in report.by_property[:SHOWN]]
    return out


def compare_properties(m: Membership, args: dict, today: datetime.date) -> dict:
    """Each property side by side: occupancy, income, expenses, net per unit, collection and turnover."""
    first, last = _span(args, today)
    card = scorecard.scorecard(m, first, month_end(last), today=today)

    def row(s) -> dict:
        return {"property": s.property.name if s.property else "All together", "units": s.units,
                "occupied": s.occupied, "occupancy": _p(s.occupancy), "income": _money(m, s.income),
                "expenses": _money(m, s.expenses), "net_operating_income": _money(m, s.noi),
                "net_per_unit": _money(m, s.noi_per_unit) if s.noi_per_unit is not None else None,
                "income_per_unit": _money(m, s.income_per_unit) if s.income_per_unit is not None else None,
                "expense_ratio": _p(s.expense_ratio), "collection_rate": _p(s.collection),
                "move_outs": s.move_outs, "turnover": _p(s.turnover)}

    return {"period": _period_name(first, last), "ranked_by": "net operating income per unit, best first",
            "properties": [row(s) for s in card.rows[:20]], "all_together": row(card.total),
            "label": "Property scorecard", "url": reverse("reports:scorecard")}


def arrears_picture(m: Membership, args: dict, today: datetime.date) -> dict:
    """Everything owed by age, by property, who owes most, and how long money stays unpaid."""
    prop, error = _property(m, args.get("property"))
    if error:
        return error
    rows, total = finance.receivables(m, today, prop)
    summary = finance.receivables_summary(rows, total)
    out = {"as_of": today.isoformat(), "owed_in_all": _money(m, total.total),
           "overdue": _money(m, summary.overdue), "not_yet_due": _money(m, total.buckets["current"]),
           "leases_owing": summary.leases, "share_owed_over_60_days_late": _p(summary.over_60_share),
           "by_age": [{"age": str(label), "amount": _money(m, amount), "share": _p(share)}
                      for _k, label, amount, share in summary.shares],
           "by_property": [{"property": r.property.name, "owed": _money(m, r.total),
                            "leases": len(r.leases),
                            "overdue": _money(m, r.total - r.buckets["current"])} for r in rows[:SHOWN]],
           "largest_balances": [
               {"unit": r.lease.unit.code, "property": r.lease.unit.property.name,
                "tenants": tenant_names(m, r.lease), "owed": _money(m, r.balance),
                "days_overdue": r.days_overdue(today),
                "url": reverse("leases:detail", args=[r.lease.public_id])} for r in summary.largest[:SHOWN]],
           "label": "Receivables", "url": reverse("reports:receivables")}
    largest = sum((r.balance for r in summary.largest[:SHOWN]), 0)
    out["largest_balances_share_of_all_owed"] = _p(metrics.rate(largest, total.total))
    return out


def payment_behaviour(m: Membership, args: dict, today: datetime.date) -> dict:
    """How tenants pay: days from the due date to paid in full, in time or late, slowest and steadiest leases."""
    first, last = _span(args, today)
    prop, error = _property(m, args.get("property"))
    if error:
        return error
    report = behaviour.behaviour(m, first, month_end(last), prop, today=today)

    def lease_row(r) -> dict:
        return {"unit": r.lease.unit.code, "property": r.lease.unit.property.name,
                "tenant": r.tenant.name if r.tenant is not None else None, "invoices_paid": len(r.days),
                "average_days_after_due": float(r.average), "slowest_days": r.slowest,
                "paid_in_time": r.in_time, "unpaid": r.unpaid,
                "url": reverse("leases:detail", args=[r.lease.public_id])}

    return {"period": _period_name(first, last), "invoices_past_due": report.invoices, "paid": report.paid,
            "still_unpaid": report.unpaid, "paid_in_time": report.in_time,
            "share_paid_in_time": _p(report.in_time_share),
            "average_days_after_due_date": float(report.average) if report.average is not None else None,
            "median_days_after_due_date": float(report.median) if report.median is not None else None,
            "negative_days_mean": "paid before the due date",
            "when_paid": [{"band": str(b.label), "invoices": b.count, "share": _p(b.share)} for b in report.bands],
            "by_due_month": [{"month": d.label, "invoices_paid": len(d.days),
                              "average_days": float(d.average) if d.average is not None else None}
                             for d in report.months],
            "slowest_leases": [lease_row(r) for r in report.slowest[:5]],
            "steadiest_leases": [lease_row(r) for r in report.steadiest[:5]],
            "label": "Payment behaviour", "url": reverse("reports:behaviour")}


def unit_performance(m: Membership, args: dict, today: datetime.date) -> dict:
    """Unit by unit over 12 months: paid in time, rent against asking, empty days, by kind of unit, pay days."""
    prop, error = _property(m, args.get("property"))
    if error:
        return error
    rep = units.unit_report(m, property=prop, today=today)
    s = rep.summary

    def unit_line(r) -> dict:
        return {"unit": r.unit.code, "property": r.unit.property.name, "status": r.status,
                "rent": _money(m, r.rent) if r.rent is not None else None,
                "months_paid_in_time": f"{r.months_paid_in_time} of {r.months_billed}",
                "collection_rate": _p(r.rate), "empty_days": r.empty_days,
                "overdue": _money(m, r.overdue) if rep.shows_arrears else None,
                "url": reverse("properties:unit", args=[r.unit.public_id])}

    billed = [r for r in rep.rows if r.expected]
    worst = sorted((r for r in billed if r.rate is not None), key=lambda r: (r.rate, r.unit.code))[:5]
    emptiest = sorted((r for r in rep.rows if r.empty_days), key=lambda r: -r.empty_days)[:5]
    below = sorted((r for r in rep.rows if r.below_asking is not None), key=lambda r: -r.below_asking)[:5]
    out = {
        "units": s.units, "occupied": s.occupied, "empty_now": s.empty_now,
        "this_month": {"billed": s.billed, "paid_in_full": s.paid, "part_paid": s.part, "unpaid": s.unpaid,
                       "not_yet_due": s.due},
        "twelve_month_collection_rate": _p(s.rate), "average_rent_of_occupied_units": (
            _money(m, s.average_rent) if s.average_rent is not None else None),
        "occupied_units_paid_in_time_every_month": s.always_in_time,
        "units_rented_below_asking": s.below_asking_units,
        "rent_given_up_below_asking_a_month": _money(m, s.below_asking),
        "empty_days_in_12_months": s.empty_days, "about_lost_to_empty_days": _money(m, s.empty_loss),
        "by_kind_of_unit": [{"kind": t.label, "units": t.units, "occupancy": _p(t.occupancy),
                             "average_rent": _money(m, t.average_rent) if t.average_rent is not None else None,
                             "average_asking": _money(m, t.average_asking) if t.average_asking is not None
                             else None, "collection_rate": _p(t.rate), "share_of_time_empty": _p(t.empty_share)}
                            for t in rep.by_type[:SHOWN]],
        "payments_by_day_of_month": [{"days": p.label, "payments": p.count, "amount": _money(m, p.amount),
                                      "share": _p(p.share)} for p in rep.pay_days],
        "lowest_collection": [unit_line(r) for r in worst],
        "most_empty_days": [unit_line(r) for r in emptiest],
        "furthest_below_asking": [unit_line(r) | {"asking": _money(m, r.unit.list_rent),
                                                  "below_by": _money(m, r.below_asking)} for r in below],
        "leases_ending_in_90_days": [{"unit": r.unit.code, "property": r.unit.property.name,
                                      "ends": r.ends_on.isoformat()} for r in s.ending[:SHOWN]],
        "label": "Unit by unit", "url": reverse("reports:units"),
    }
    if rep.too_many:
        out["note"] = "there are many units: ask for one property to see unit-level lists"
        for key in ("lowest_collection", "most_empty_days", "furthest_below_asking"):
            out[key] = []
    return out


def lease_movement(m: Membership, args: dict, today: datetime.date) -> dict:
    """Move-ins, move-outs, renewals, turnover, length of stay, rent rises and leases about to end."""
    first, last = _span(args, today)
    prop, error = _property(m, args.get("property"))
    if error:
        return error
    r = operations.leases(m, first, month_end(last), prop, today=today)
    return {
        "period": _period_name(first, last), "active_leases_now": r.active_now, "move_ins": r.move_ins,
        "move_outs": r.move_outs, "renewals": r.renewals, "turnover": _p(r.turnover),
        "average_months_stayed_by_leases_that_ended": float(r.average_months) if r.average_months else None,
        "length_of_stay_of_leases_that_ended": [{"stay": label, "leases": n} for label, n in r.stays],
        "how_long_current_leases_have_run": [{"time": label, "leases": n} for label, n in r.tenure],
        "rent_increases": r.increases, "average_increase": _p(r.average_increase),
        "days_a_unit_stood_empty_before_re_let": float(r.relet_days) if r.relet_days is not None else None,
        "units_re_let": r.relets, "leases_not_raised_in_a_year": r.stale_total,
        "ending_within_days": [{"days": d, "leases": n} for d, n in r.windows],
        "past_end_date_still_active": len(r.holding_over),
        "label": "Leases and tenants", "url": reverse("reports:leases"),
    }


def repair_stats(m: Membership, args: dict, today: datetime.date) -> dict:
    """Repairs: how many, how fast, what they cost, by priority, kind, property and supplier, and repeat problems."""
    first, last = _span(args, today)
    prop, error = _property(m, args.get("property"))
    if error:
        return error
    r = operations.maintenance(m, first, month_end(last), prop)

    def rows(group) -> list[dict]:
        return [{"name": x.label, "requests": x.total, "done": x.resolved, "open": x.open, "late": x.overdue,
                 "average_hours_to_finish": float(x.hours) if x.hours is not None else None,
                 "cost": _money(m, x.cost)} for x in group[:SHOWN]]

    return {
        "period": _period_name(first, last), "requests": r.total, "finished": r.resolved,
        "share_finished": _p(r.resolved_share), "cancelled": r.cancelled, "open_now": r.open_now,
        "late_now": r.overdue_now, "not_started": r.waiting_to_start,
        "average_hours_to_finish": float(r.avg_hours) if r.avg_hours is not None else None,
        "finished_in_time": r.fixed_in_time, "share_finished_in_time": _p(r.fixed_in_time_share),
        "cost": _money(m, r.cost), "by_priority": rows(r.by_priority), "by_kind": rows(r.by_kind),
        "by_property": rows(r.by_property), "by_supplier": rows(r.by_supplier),
        "by_month": [{"month": x["label"], "reported": x["reported"], "finished": x["resolved"]}
                     for x in r.months],
        "repeat_problems": [{"property": x["property"], "unit": x["unit"], "kind": str(x["kind"]),
                             "times": x["count"]} for x in r.repeats[:SHOWN]],
        "label": "Maintenance report", "url": reverse("reports:maintenance"),
    }


def payment_stats(m: Membership, args: dict, today: datetime.date) -> dict:
    """Money received in a period by method, month and account, and payments still waiting for review."""
    first, last = _span(args, today)
    prop, error = _property(m, args.get("property"))
    if error:
        return error
    r = operations.payments(m, first, month_end(last), prop)
    return {
        "period": _period_name(first, last), "confirmed": _money(m, r.confirmed),
        "confirmed_payments": r.confirmed_count,
        "reversed_or_rejected": _money(m, r.undone),
        "by_state": [{"state": label, "payments": n, "amount": _money(m, a)} for _k, label, n, a in r.states],
        "by_method": [{"method": label, "payments": n, "amount": _money(m, a), "share": _p(share)}
                      for label, n, a, share in r.methods],
        "by_month": [{"month": label, "payments": n, "amount": _money(m, a)} for _d, label, n, a in r.months],
        "by_account": [{"account": label, "payments": n, "amount": _money(m, a)} for label, n, a in r.accounts[:SHOWN]],
        "waiting_for_review": {"payments": r.waiting, "amount": _money(m, r.waiting_amount),
                               "oldest": r.oldest_waiting.isoformat() if r.oldest_waiting else None},
        "label": "Payments report", "url": reverse("reports:payments"),
    }


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

_PROPERTY = {"type": "string", "description": "Optional: a property name or code, to look at one property only."}
_RANGE = {
    "from": {"type": "string", "description": "Optional: first month, YYYY-MM. Default: 5 months before 'to'."},
    "to": {"type": "string", "description": "Optional: last month, YYYY-MM. Default: this month. At most 12 months."},
}


def _tool(name: str, description: str, run, *, month=False, ranged=True) -> Tool:
    props = {"property": _PROPERTY}
    if month:
        props["month"] = {"type": "string", "description": "Optional: YYYY-MM. Default: this month."}
    if ranged:
        props.update(_RANGE)
    return Tool(name, description, {"type": "object", "properties": props}, run)


TOOLS: tuple[Tool, ...] = (
    _tool("month_review", "Explain one month like the dashboard: rent expected, collected and still to collect, "
          "against last month and the same month last year, collection by property, cash by method, empty units, "
          "tenant payment grades, what is overdue by age, and the dashboard's own highlights. Start here for "
          "'how did we do', 'what happened this month' or 'why is it down'.", month_review, month=True,
          ranged=False),
    _tool("trend", "Month by month rent expected, collected, collection rate, cash and occupancy over up to 12 "
          "months, with the best and worst month and the change from the first month to the last. Use for "
          "'is it getting better', trends and seasons.", trend),
    _tool("money", "Profit and loss for a period against the period before: income, expenses, net operating "
          "income, margin, expense ratio, month by month, where expenses went by category, and each property's "
          "result. Use for profit, costs and 'where did the money go'.", money),
    _tool("compare_properties", "Properties side by side for a period: occupancy, income, expenses, net per unit, "
          "collection rate and turnover, ranked best first. Use for 'which property does best / worst'.",
          compare_properties),
    _tool("arrears_picture", "Everything owed today: by age (not yet due to over 90 days), by property, the "
          "largest balances and the share owed more than 60 days late. Use for 'how bad are arrears'.",
          arrears_picture, ranged=False),
    _tool("payment_behaviour", "How tenants pay: share paid in time, average and median days from the due date, "
          "invoices by how late they were paid, by month, and the slowest and steadiest leases.",
          payment_behaviour),
    _tool("unit_performance", "Unit by unit over the last 12 months: this month's paid / part / unpaid units, "
          "collection rate, units paid in time every month, rent below asking, empty days and what they cost, by "
          "kind of unit, which days of the month people pay, and the weakest units.", unit_performance,
          ranged=False),
    _tool("lease_movement", "Tenants coming and going: move-ins, move-outs, renewals, turnover, length of stay, "
          "rent increases, days to re-let a unit and leases about to end.", lease_movement),
    _tool("repair_stats", "Repairs: how many, how fast they were finished, how many were in time, cost, by "
          "priority, kind, property and supplier, month by month, and units with repeat problems.", repair_stats),
    _tool("payment_stats", "Money received in a period by method (M-Pesa, bank, cash), month and account, and "
          "payments waiting for review.", payment_stats),
)
