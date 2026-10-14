"""The report centre, maintenance and message delivery pages (D-132). Thin views; reports.operations does the work."""

import csv
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can
from accounts.portfolio import agency_tools
from audit import services as audit
from core import charts

from . import behaviour, deposit_report, metrics, operations, periods, report_notes, scorecard, xlsx
from .views import _csv_response, _finance_filters, _pct


def _period_context(request, prop):
    """The period chosen, and what the filter form and the links between pages need."""
    today = timezone.localdate()
    period = periods.from_request(request.GET, today)
    carry = dict(period.query())
    if prop is not None:
        carry["property"] = str(prop.public_id)
    return period, {"period": period, "presets": periods.PRESETS, "carry": carry, "today": today}


def report_groups(m) -> list:
    """The reports by the question they answer: [(group title, [(url name, label, text, may open)])] (D-148).

    The report centre lists them, and each report's own page steps to the one before and after in this order.
    """
    financial = can(m, "reports.view_financial")
    # Fees and owner statements are for those who manage other people's property (D-150).
    owners = financial and agency_tools(m.organization)
    return [
        (_("Executive summary"), [
            ("reports:dashboard", _("Dashboard"), _("Rent, collections, arrears, occupancy and profit this month."),
             can(m, "dashboard.view_financial")),
        ]),
        (_("Rent and collections"), [
            ("reports:income", _("Rental income pack"), _("Rent received by month and property, for tax."),
             financial),
            ("reports:units", _("Unit by unit"), _("Which units pay in time, and which stand empty."), financial),
        ]),
        (_("Arrears and debt"), [
            ("reports:receivables", _("Aged receivables"), _("Everything owed, by property and by how late."),
             financial),
            ("reports:arrears", _("Arrears analysis"),
             _("Who owes, how late, who pays late again and again, and how much was recovered."),
             financial and can(m, "invoices.view")),
        ]),
        (_("Properties and deposits"), [
            ("reports:scorecard", _("Property scorecard"),
             _("Each property ranked: occupancy, collection, net income per unit, costs and turnover."),
             financial),
            ("reports:deposits", _("Deposits"),
             _("Held, taken, returned and deducted, and deposits to settle on ended leases."), financial),
        ]),
        (_("Expenses and profit"), [
            ("reports:profit_loss", _("Profit and loss"), _("Income, expenses and net operating income."),
             financial),
            ("reports:cash_flow", _("Cash flow"), _("Money in and out by month."), financial),
            ("reports:fees", _("Management fees"), _("Fees earned from owners."), owners),
            ("reports:owner_statement", _("Owner statement"), _("What each owner earned and is owed."), owners),
        ]),
        (_("Payment transactions"), [
            ("reports:payments", _("Payments received"),
             _("Confirmed, waiting, reversed and rejected payments, by method and account."),
             can(m, "payments.view")),
        ]),
        (_("Tenants and leases"), [
            ("reports:behaviour", _("How tenants pay"),
             _("Days from the due date to payment, late payers and steady payers."),
             financial and can(m, "invoices.view")),
            ("reports:leases", _("Leases and turnover"),
             _("Move-ins, move-outs, renewals, length of stay, rent increases, time to re-let and leases about to end."), can(m, "leases.view")),
        ]),
        (_("Maintenance and operations"), [
            ("reports:maintenance", _("Maintenance"),
             _("Workload, resolution times, repeat problems and repair costs."), can(m, "maintenance.view")),
            ("reports:delivery", _("Message delivery"), _("How many SMS, WhatsApp and email messages arrived."),
             can(m, "messages.view")),
            ("reports:utilities", _("Water and electricity"),
             _("Meter use, what was billed for it and how much of it was paid."), can(m, "meters.view")),
        ]),
        (_("For the accountant"), [
            ("reports:accounting_export", _("Accounting export"), _("Books as CSV files in one download."),
             can(m, "reports.export")),
        ]),
    ]


class ReportCentreView(CapabilityRequiredMixin, View):
    """Every report in one place, by the question it answers, showing only those the member may open."""

    template_name = "reports/centre.html"
    required_capability = "reports.view_basic"

    def get(self, request):
        m = request.membership
        financial = can(m, "reports.view_financial")
        groups = report_groups(m)
        facts = self._facts(m) if financial else {}
        # The reader (D-148): every report the member may open, in order, read one at a time beside the list.
        # ?r= names the one on show by its address name without "reports:" (e.g. "arrears"); otherwise the first.
        reports = [{"key": name.split(":", 1)[1], "url": reverse(name), "label": str(label), "text": str(text),
                    "group": str(title), "facts": facts.get(name, [])}
                   for title, items in groups for name, label, text, ok in items if ok]
        groups = [(title, [r for r in reports if r["group"] == str(title)]) for title, _items in groups]
        groups = [g for g in groups if g[1]]
        keys = [r["key"] for r in reports]
        index = keys.index(request.GET["r"]) if request.GET.get("r") in keys else 0
        return render(request, self.template_name, {
            "groups": groups, "reports": reports, "current": reports[index] if reports else None,
            "position": index + 1,
            "previous": reports[index - 1] if index > 0 else None,
            "next": reports[index + 1] if index + 1 < len(reports) else None,
            "reader": [{k: r[k] for k in ("key", "url", "label", "text", "group")} for r in reports],
            "cur": m.organization.currency, "month": timezone.localdate()})

    @staticmethod
    def _facts(m) -> dict:
        """Live headline figures for each report card: [(label, value, kind, tone)], kind money or text.

        Best effort: a member without the dashboard figures, or a failure, gets the plain cards.
        """
        try:
            board = metrics.dashboard(m)
        except Exception:  # the centre must open even when a figure cannot be worked out
            return {}
        c, occ, arrears = board.current, board.occupancy, board.arrears
        rate = f"{c.rate * 100:.0f}%" if c.rate is not None else "-"
        occ_rate = f"{occ.rate * 100:.0f}%" if occ.rate is not None else "-"
        owed = ("Overdue", arrears.total, "money", "danger" if arrears.total else "")
        in_arrears = (_("In arrears"), f"{arrears.leases} " + _("leases"), "text", "danger" if arrears.leases else "")
        facts = {
            "reports:dashboard": [(_("Collected"), c.collected, "money", ""), (_("Collection rate"), rate, "text", ""),
                                  (_("Occupancy"), occ_rate, "text", "")],
            "reports:income": [(_("Collected"), c.collected, "money", ""), (_("Expected"), c.expected, "money", "")],
            "reports:units": [(_("Occupied"), f"{occ.occupied} / {occ.rentable}", "text", ""),
                              (_("Empty"), f"{occ.vacant}", "text", "warning" if occ.vacant else "")],
            "reports:receivables": [(_("Overdue"),) + owed[1:], in_arrears],
            "reports:arrears": [(_("Overdue"),) + owed[1:], in_arrears],
            "reports:cash_flow": [(_("Cash in"), c.cash, "money", "")],
        }
        if board.unmatched is not None:
            facts["reports:payments"] = [(_("Cash received"), c.cash, "money", ""),
                                         (_("Unmatched M-Pesa"), f"{board.unmatched.count}", "text",
                                          "warning" if board.unmatched.count else "")]
        return facts


class _OperationsView(CapabilityRequiredMixin, View):
    template_name = ""
    slug = ""

    def csv_rows(self, report) -> list[list]:
        raise NotImplementedError

    def build(self, request, period, prop):
        raise NotImplementedError

    def context(self, request, report, prop) -> dict:
        return {}

    def get(self, request):
        m = request.membership
        prop, choices = _finance_filters(request)
        period, ctx = _period_context(request, prop)
        report = self.build(request, period, prop)
        fmt = request.GET.get("format")
        if fmt in ("csv", "xlsx"):
            if not can(m, "reports.export"):
                raise PermissionDenied("reports.export")
            audit.record("reports.export", actor=request.user, organization=m.organization, request=request,
                         changes={"report": [None, self.slug], "period": [None, period.label],
                                  "format": [None, fmt]})
            name = f"{self.slug}-{period.start.isoformat()}-{period.end.isoformat()}"
            rows = [[_("Scope"), prop.name if prop else _("All your properties")],
                    [_("Period"), period.start.isoformat(), period.end.isoformat()], *self.csv_rows(report)]
            if fmt == "xlsx":
                return xlsx.response(f"{name}.xlsx", self.slug, rows)
            response = _csv_response(f"{name}.csv")
            csv.writer(response).writerows(rows)
            return response
        query = request.GET.copy()
        query["format"] = "csv"
        return render(request, self.template_name, {
            "report": report, "properties": choices, "selected": prop, "can_export": can(m, "reports.export"),
            "notes": report_notes.NOTES.get(self.slug, lambda *_a: [])(report, m.organization.currency),
            "csv_query": query.urlencode(), "currency": m.organization.currency, **ctx,
            **self.context(request, report, prop)})


class MaintenanceReportView(_OperationsView):
    template_name = "reports/maintenance.html"
    required_capability = "maintenance.view"
    slug = "maintenance"

    def build(self, request, period, prop):
        return operations.maintenance(request.membership, period.start, period.end, prop)

    def context(self, request, report, prop):
        cur = request.membership.organization.currency
        specs = {}
        if report.months:
            specs["months"] = charts.chart(
                "bar", [r["label"] for r in report.months],
                [charts.series(_("Reported"), [r["reported"] for r in report.months], "1"),
                 charts.series(_("Resolved"), [r["resolved"] for r in report.months], "2")], axis="count")
        if report.by_kind:
            specs["kinds"] = charts.chart("bar", [r.label for r in report.by_kind],
                                          [charts.series(_("Requests"), [r.total for r in report.by_kind], "1")],
                                          axis="count")
        if report.cost:
            places = [(r.label, r.cost) for r in report.by_property if r.cost]
            specs["costs"] = charts.doughnut(_("Repair costs"), charts.fold(places, _("Other")), charts.SERIES,
                                             currency=cur)
        return {"specs": specs}

    def csv_rows(self, report):
        head = ["", "Requests", "Resolved", "Open now", "Overdue now", "Average hours to resolve", "Cost"]
        rows = [[_("Summary")], head[1:], [report.total, report.resolved, report.open_now, report.overdue_now,
                                           report.avg_hours or "", report.cost]]
        for title, group in ((_("By priority"), report.by_priority), (_("By kind"), report.by_kind),
                             (_("By property"), report.by_property), (_("By supplier"), report.by_supplier)):
            rows += [[], [title], head]
            rows += [[r.label, r.total, r.resolved, r.open, r.overdue, r.hours or "", r.cost] for r in group]
        rows += [[], [_("Repeated problems")], ["Property", "Unit", "Kind", "Requests"]]
        rows += [[r["property"], r["unit"], r["kind"], r["count"]] for r in report.repeats]
        return rows


class DeliveryReportView(_OperationsView):
    template_name = "reports/delivery.html"
    required_capability = "messages.view"
    slug = "message-delivery"

    def get(self, request):
        if request.GET.get("property"):
            raise Http404  # messages belong to the organization, not a property
        return super().get(request)

    def build(self, request, period, prop):
        return operations.delivery(request.membership, period.start, period.end)

    def context(self, request, report, prop):
        specs = {}
        status = operations.Message.Status
        if report.channels:
            colors = {status.DELIVERED: "1", status.SENT: "2", status.QUEUED: "neutral", status.SKIPPED: "late-1",
                      status.FAILED: "late-4"}
            specs["channels"] = charts.chart(
                "bar", [c.label for c in report.channels],
                [charts.series(label, [c.counts.get(s, 0) for c in report.channels], colors.get(s, "neutral"))
                 for s, label in status.choices], axis="count",
                links=[reverse("notifications:log") + "?" + urlencode({
                    "channel": c.channel, "date_from": report.start.isoformat(), "date_to": report.end.isoformat()})
                    for c in report.channels])
        return {"specs": specs, "status": status}

    def csv_rows(self, report):
        S = operations.Message.Status
        rows = [["Channel", "Total", "Sent", "Delivered", "Failed", "Skipped", "Queued", "Failure rate %"]]
        for c in report.channels:
            rows.append([c.label, c.total, c.counts.get(S.SENT, 0), c.counts.get(S.DELIVERED, 0),
                         c.counts.get(S.FAILED, 0), c.counts.get(S.SKIPPED, 0), c.counts.get(S.QUEUED, 0),
                         _pct(c.failure_rate)])
        rows += [[], [_("Why messages were skipped")], *[[a, b] for a, b in report.skipped]]
        rows += [[], [_("Errors on failed messages")], *[[a, b] for a, b in report.errors]]
        return rows


class PaymentsReportView(_OperationsView):
    template_name = "reports/payments.html"
    required_capability = "payments.view"
    slug = "payments"

    def build(self, request, period, prop):
        return operations.payments(request.membership, period.start, period.end, prop)

    def context(self, request, report, prop):
        cur = request.membership.organization.currency
        specs = {}
        extra = {"property": str(prop.public_id)} if prop is not None else {}
        if report.months:
            specs["months"] = charts.chart("bar", [label for _m, label, _n, _a in report.months],
                                           [charts.series(_("Money received"), [a for *_x, a in report.months], "1")],
                                           currency=cur,
                                           links=[reverse("payments:list") + "?" + urlencode({
                                               **extra, "status": "confirmed", "date_from": m.isoformat(),
                                               "date_to": min(metrics.month_end(m), report.end).isoformat()})
                                               for m, _l, _n, _a in report.months])
        if report.methods:
            specs["methods"] = charts.doughnut(_("Money received"), [(label, a) for label, _n, a, _s in report.methods],
                                               charts.SERIES, currency=cur)
        query = {"date_from": report.start.isoformat(), "date_to": report.end.isoformat()}
        if prop is not None:
            query["property"] = str(prop.public_id)
        states = [(key, label, n, amount, reverse("payments:list") + "?" + urlencode({**query, "status": key}))
                  for key, label, n, amount in report.states]
        return {"specs": specs, "states": states}

    def csv_rows(self, report):
        rows = [["State", "Payments", "Amount"]]
        rows += [[label, n, a] for _k, label, n, a in report.states]
        rows += [[], [_("Confirmed, by method")], ["Method", "Payments", "Amount", "Share %"]]
        rows += [[label, n, a, _pct(s)] for label, n, a, s in report.methods]
        rows += [[], [_("Confirmed, by month")], ["Month", "Payments", "Amount"]]
        rows += [[label, n, a] for _m, label, n, a in report.months]
        rows += [[], [_("Confirmed, by account")], ["Account", "Payments", "Amount"]]
        rows += [[name, n, a] for name, n, a in report.accounts]
        rows += [[], [_("Waiting for review now"), report.waiting, report.waiting_amount]]
        return rows


class LeasesReportView(_OperationsView):
    template_name = "reports/leases.html"
    required_capability = "leases.view"
    slug = "leases"

    def build(self, request, period, prop):
        return operations.leases(request.membership, period.start, period.end, prop)

    def context(self, request, report, prop):
        specs = {}
        if any(n for _label, n in report.stays) or any(n for _label, n in report.tenure):
            specs["stays"] = charts.chart("bar", [_(label) for label, _n in report.stays], [
                charts.series(_("Leases that ended"), [n for _label, n in report.stays], "2"),
                charts.series(_("Active now"), [n for _label, n in report.tenure], "1")], axis="count")
        return {"specs": specs}

    def csv_rows(self, report):
        rows = [["Leases in force", report.in_force], ["Move-ins", report.move_ins], ["Move-outs", report.move_outs],
                ["Renewals", report.renewals], ["Turnover %", _pct(report.turnover)],
                ["Average months of leases that ended", report.average_months or ""],
                ["Rent increases", report.increases], ["Average increase %", _pct(report.average_increase)],
                ["Average days to re-let", report.relet_days or ""], ["Rent not raised for a year", report.stale_total],
                []]
        rows += [["Length of stay, leases that ended"]] + [[label, n] for label, n in report.stays] + [[]]
        rows += [["Leases active now by time run"]] + [[label, n] for label, n in report.tenure] + [[]]
        for title, lines in ((_("Ending in the next 90 days"), report.expiring),
                             (_("Past their end date and still active"), report.holding_over)):
            rows += [[title], ["Lease", "Property", "Unit", "Tenant", "End date", "Days left"]]
            rows += [[x.lease.number, x.lease.unit.property.name, x.lease.unit.code,
                      x.tenant.name if x.tenant else "", x.lease.end_date.isoformat(), x.days] for x in lines]
            rows.append([])
        return rows


class PaymentBehaviourView(_OperationsView):
    template_name = "reports/behaviour.html"
    required_capability = "reports.view_financial"
    slug = "payment-behaviour"

    def build(self, request, period, prop):
        return behaviour.behaviour(request.membership, period.start, period.end, prop)

    def context(self, request, report, prop):
        specs = {}
        if report.paid:
            specs["bands"] = charts.chart("bar", [str(b.label) for b in report.bands],
                                          [charts.series(_("Invoices"), [b.count for b in report.bands], "1")],
                                          axis="count")
        points = [m for m in report.months if m.average is not None]
        if len(points) > 1:
            specs["months"] = charts.chart("line", [m.label for m in points],
                                           [charts.series(_("Average days from the due date"),
                                                          [m.average for m in points], "2")], axis="count")
        return {"specs": specs, "lists": [(_("Slowest to pay"), report.slowest),
                                          (_("Steadiest payers"), report.steadiest)]}

    def csv_rows(self, report):
        rows = [["Invoices past due", report.invoices], ["Paid in full", report.paid], ["Not paid", report.unpaid],
                ["Paid in time", report.in_time], ["Average days from due date", report.average or ""],
                ["Median days", report.median or ""], [], ["Days from the due date", "Invoices", "Share %"]]
        rows += [[str(b.label), b.count, _pct(b.share)] for b in report.bands]
        rows += [[], ["Month", "Paid invoices", "Average days"]]
        rows += [[m.label, len(m.days), m.average or ""] for m in report.months]
        for title, lines in ((_("Slowest to pay"), report.slowest), (_("Steadiest payers"), report.steadiest)):
            rows += [[], [title], ["Lease", "Property", "Unit", "Tenant", "Paid invoices", "Unpaid", "Average days",
                                   "Slowest"]]
            rows += [[x.lease.number, x.lease.unit.property.name, x.lease.unit.code,
                      x.tenant.name if x.tenant else "", len(x.days), x.unpaid, x.average, x.slowest]
                     for x in lines]
        return rows


class ScorecardView(_OperationsView):
    template_name = "reports/scorecard.html"
    required_capability = "reports.view_financial"
    slug = "property-scorecard"

    def build(self, request, period, prop):
        return scorecard.scorecard(request.membership, period.start, period.end, prop)

    def context(self, request, report, prop):
        specs = {}
        rows = [r for r in report.rows if r.units]
        if len(rows) > 1:
            pct = lambda v: None if v is None else round(float(v) * 100, 1)  # noqa: E731
            specs["rates"] = charts.chart("bar", [r.property.name for r in rows], [
                charts.series(_("Occupied"), [pct(r.occupancy) for r in rows], "1"),
                charts.series(_("Rent collected"), [pct(r.collection) for r in rows], "2")],
                axis="pct", horizontal=True)
            specs["noi"] = charts.chart("bar", [r.property.name for r in rows], [
                charts.series(_("Net income per unit"), [r.noi_per_unit for r in rows], "1")],
                currency=request.membership.organization.currency, horizontal=True)
        return {"specs": specs}

    def csv_rows(self, report):
        rows = [["Property", "Units", "Occupied", "Occupancy %", "Income", "Expenses", "Net operating income",
                 "Net per unit", "Expenses % of income", "Collection %", "Move-outs", "Turnover %"]]
        for r in (*report.rows, report.total):
            rows.append([r.property.name if r.property else "Total", r.units, r.occupied, _pct(r.occupancy),
                         r.income, r.expenses, r.noi, r.noi_per_unit or "", _pct(r.expense_ratio),
                         _pct(r.collection), r.move_outs, _pct(r.turnover)])
        return rows


class DepositsReportView(_OperationsView):
    template_name = "reports/deposits.html"
    required_capability = "reports.view_financial"
    slug = "deposits"

    def build(self, request, period, prop):
        return deposit_report.deposits(request.membership, period.start, period.end, prop)

    def context(self, request, report, prop):
        specs = {}
        cur = request.membership.organization.currency
        if report.enabled and report.by_type:
            specs["held"] = charts.doughnut(_("Held"), report.by_type, charts.SERIES, currency=cur)
        if report.enabled and any(a or b for _m, a, b in report.months):
            specs["moves"] = charts.chart("bar", [m for m, _a, _b in report.months], [
                charts.series(_("Taken"), [a for _m, a, _b in report.months], "1"),
                charts.series(_("Returned or deducted"), [b for _m, _a, b in report.months], "2")], currency=cur)
        return {"specs": specs, "lists": [
            (_("Ended, still holding money"),
             _("The lease is over, so the tenant is owed a refund or a statement of deductions."), report.to_settle),
            (_("Ending soon, holding money"), _("Leases ending or tenants moving out in the next 90 days."),
             report.ending)]}

    def csv_rows(self, report):
        rows = [["Held now", report.held], ["Leases holding a deposit", report.holding],
                ["Taken in the period", report.received], ["Deducted", report.deducted],
                ["Refunded", report.refunded], ["Corrections", report.corrections], [], ["Deposit", "Held"]]
        rows += [[label, amount] for label, amount in report.by_type]
        for title, lines, last in ((_("Ended, still holding money"), report.to_settle, "Days since it ended"),
                                   (_("Ending soon, holding money"), report.ending, "Days until it ends")):
            rows += [[], [title], ["Lease", "Property", "Unit", "Tenant", "Held", last]]
            rows += [[x.lease.number, x.lease.unit.property.name, x.lease.unit.code,
                      x.tenant.name if x.tenant else "", x.held, x.days] for x in lines]
        return rows


class ArrearsReportView(_OperationsView):
    template_name = "reports/arrears.html"
    required_capability = "reports.view_financial"
    slug = "arrears"

    def build(self, request, period, prop):
        raw = request.GET.get("min", "").strip()
        try:
            floor = Decimal(raw) if raw else None
        except InvalidOperation:
            floor = None
        if floor is not None and floor <= 0:
            floor = None
        return operations.arrears(request.membership, period.start, period.end, prop,
                                  today=timezone.localdate(), min_amount=floor)

    def context(self, request, report, prop):
        cur = request.membership.organization.currency
        specs = {}
        points = [p for p in report.history if p.day is not None]
        if points:
            specs["trend"] = charts.chart("bar", [p.label for p in points], [
                charts.series(_("1-30 days"), [p.d1_30 for p in points], "1"),
                charts.series(_("31-60 days"), [p.d31_60 for p in points], "2"),
                charts.series(_("61-90 days"), [p.d61_90 for p in points], "3"),
                charts.series(_("Over 90 days"), [p.d90 for p in points], "4")], currency=cur, stacked=True)
        slices = [(str(label), amount) for key, label, amount, _s in report.buckets if amount]
        if slices:
            specs["ageing"] = charts.doughnut(_("Owed"), slices, charts.SERIES, currency=cur)
            specs["ageing"]["links"] = [reverse("reports:receivables") + (
                "?" + urlencode({"property": str(prop.public_id)}) if prop is not None else "")] * len(slices)
        return {"specs": specs, "min_value": report.min_amount or ""}

    def csv_rows(self, report):
        rows = [["Owed in total", report.owed], ["Overdue", report.overdue], ["Leases overdue", report.leases],
                ["Invoices overdue", report.invoices],
                ["Late rent recovered in the period", report.recovered], [], [_("By how late")],
                ["Band", "Amount", "Share %"]]
        rows += [[label, a, _pct(s)] for _k, label, a, s in report.buckets]
        rows += [[], [_("By property")], ["Property", "Overdue", "Leases"]]
        rows += [[n, a, c] for n, a, c in report.by_property]
        rows += [[], [_("By lease")], ["Lease", "Property", "Unit", "Tenant", "Overdue", "Total owed", "Days late",
                                      "Invoices"]]
        rows += [[x.row.lease.number, x.row.lease.unit.property.name, x.row.lease.unit.code,
                  x.tenant.name if x.tenant else "", x.overdue, x.row.balance, x.days, x.invoices]
                 for x in report.top]
        rows += [[], [_("Paid late again and again")], ["Lease", "Property", "Unit", "Tenant", "Late months",
                                                         "Months billed"]]
        rows += [[x.lease.number, x.lease.unit.property.name, x.lease.unit.code,
                  x.tenant.name if x.tenant else "", x.late, x.billed] for x in report.recurring]
        rows += [[], [_("Overdue on the last snapshot of each month")], ["Month", "Overdue", "Leases"]]
        rows += [[p.label, p.total, p.leases] for p in report.history if p.day is not None]
        return rows


class UtilitiesReportView(_OperationsView):
    template_name = "reports/utilities.html"
    required_capability = "meters.view"
    slug = "utilities"

    def build(self, request, period, prop):
        return operations.utilities(request.membership, period.start, period.end, prop)

    def context(self, request, report, prop):
        cur = request.membership.organization.currency
        specs = {}
        names = {key: str(label) for key, label in operations.Meter.Utility.choices}
        kinds = [k for k in names if k in report.usage]
        if report.months and kinds:
            specs["months"] = charts.chart(
                "bar", [label for _m, label, _v in report.months],
                [charts.series(names[k], [v.get(k, 0) for _m, _l, v in report.months], str(i + 1))
                 for i, k in enumerate(kinds)], axis="count", stacked=True)
        if report.show_money and report.billed:
            specs["money"] = charts.doughnut(_("Billed"), [(_("Paid"), report.collected),
                                                          (_("Invoiced, not paid"), report.invoiced - report.collected),
                                                          (_("Not invoiced yet"), report.uninvoiced)],
                                             charts.SERIES, currency=cur)
        return {"specs": specs, "names": names}

    def csv_rows(self, report):
        rows = [["Use", "Readings", "Billed", "Invoiced", "Collected (estimate)"]]
        rows += [[r.name, r.readings, r.billed if report.show_money else "", r.invoiced if report.show_money else "",
                  r.collected if report.show_money else ""] for r in report.by_utility]
        for title, lines in ((_("By property"), report.by_property), (_("Biggest users"), report.by_meter)):
            rows += [[], [title], ["Name", "Units used", "Readings", "Billed", "Collected (estimate)"]]
            rows += [[r.name, r.usage, r.readings, r.billed if report.show_money else "",
                      r.collected if report.show_money else ""] for r in lines]
        rows += [[], [_("Units used by month")], ["Month", "Water", "Electricity"]]
        rows += [[label, v.get("WATER", 0), v.get("ELECTRICITY", 0)] for _m, label, v in report.months]
        return rows
