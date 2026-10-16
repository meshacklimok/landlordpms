"""Reports: the dashboard (D-051), the annual rental income pack (D-050), the owner statement (D-053)
and payments to owners (D-058).

Thin views; reports.metrics and reports.income do the work.
"""

import csv

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can, visible_properties
from audit import services as audit
from billing import selectors as billing_selectors
from core import charts, reportcache
from properties.models import Property

from . import (
    accounting,
    fees,
    finance,
    forms,
    income,
    insights,
    metrics,
    owners,
    patterns,
    snapshots,
    statements,
    units,
)
from .models import OwnerRemittance, OwnerStatementSend
from .pdf import render_pack, render_statement

CSV_KINDS = ("months", "properties", "receipts")


def _csv_response(filename: str) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("﻿")  # BOM so Excel reads UTF-8
    return response


# ---------------------------------------------------------------------------
# Dashboard (D-051)
# ---------------------------------------------------------------------------


def _dashboard(request):
    m = request.membership
    form = forms.DashboardFilterForm(request.GET, properties=visible_properties(m, Property.objects.all())
                                     .order_by("name"))
    today = timezone.localdate()
    month = form.value("month")
    # Next month may already be billed; anything later is empty, so fall back to this month.
    if month is None or month > metrics.add_months(metrics.month_start(today), 1):
        month = today
    prop = form.value("property")
    board = reportcache.cached(m, "dashboard", (month, prop and prop.pk),
                               lambda: metrics.dashboard(m, month=month, property=prop, today=today, analysis=True))
    return form, board


def _pct(value) -> str:
    return "" if value is None else f"{value * 100:.1f}"


class DashboardView(CapabilityRequiredMixin, View):
    template_name = "reports/dashboard.html"
    required_capability = "dashboard.view_financial"

    def get(self, request):
        form, board = _dashboard(request)
        prop = board.scope.selected_property
        first, last = board.month, metrics.month_end(board.month)
        payments_query = {"status": "confirmed", "date_from": first.isoformat(), "date_to": last.isoformat()}
        if prop is not None:
            payments_query["property"] = str(prop.public_id)
        chart = {
            "labels": [row.label for row in board.trend],
            "expected": [float(row.expected) for row in board.trend],
            "collected": [float(row.collected) for row in board.trend],
            "cash": [float(row.cash) for row in board.trend],
            "aging_labels": [str(label) for _key, label, _amount in board.arrears.buckets],
            "aging": [float(amount) for _key, _label, amount in board.arrears.buckets],
            "names": {"expected": _("Rent expected"), "collected": _("Rent collected"), "cash": _("Cash received"),
                      "arrears": _("Arrears")},
            "currency": board.currency,
        }
        history = snapshots.history(board.scope, board.month)
        # Arrears today against the last day recorded last month (D-110); only for this month, as arrears are today's.
        before = history[-2] if board.is_current_month and len(history) > 1 else None
        arrears_change = (metrics.Change(board.arrears.total, before.total, before.label)
                          if before is not None and before.day is not None else None)
        ch = board.changes
        compare = [(_("Rent expected"), False, *ch["expected"]), (_("Rent collected"), False, *ch["collected"]),
                   (_("Collection rate"), True, *ch["rate"]), (_("Cash received"), False, *ch["cash"])]
        if any(p.day for p in history):
            chart["history_labels"] = [p.label for p in history]
            chart["history"] = [
                {"label": str(label), "data": [None if p.day is None else float(getattr(p, key)) for p in history]}
                for key, label, *_rest in billing_selectors.AGING_BUCKETS if key != "current"]
        m = request.membership
        chart.update(self.breakdowns(board))
        # Profit over the same 12 months (D-113), for members who may see profit and loss.
        money = None
        if can(m, "reports.view_financial"):
            money_from = metrics.add_months(board.month, 1 - metrics.TREND_MONTHS)
            money = reportcache.cached(m, "dashboard-money", (money_from, board.month, prop and prop.pk),
                                       lambda: finance.money_report(m, money_from, board.month, prop))
            chart["money"] = {
                "labels": [row.label for row in money.months],
                "income": [float(row.income) for row in money.months],
                "expenses": [float(row.expenses) for row in money.months],
                "noi": [float(row.noi) for row in money.months],
                "categories": [[str(name), float(amount)] for name, amount in money.expense_categories],
                "places": [[place.property.name, float(place.figures.noi)] for place in money.by_property],
                "names": {"income": _("Income"), "expenses": _("Expenses"), "noi": _("Net operating income")},
            }
        recorded = [p for p in history if p.day is not None]
        spark = {"collected": [row.collected for row in board.trend], "expected": [row.expected for row in board.trend],
                 "rate": [row.rate for row in board.trend], "cash": [row.cash for row in board.trend],
                 "occupancy": [row.rate for row in board.occupancy_trend],
                 "arrears": [p.total if p.day is not None else None for p in history] if len(recorded) > 1 else [],
                 "noi": [row.noi for row in money.months] if money else [],
                 "expenses": [row.expenses for row in money.months] if money else []}
        money_month = money.months[-1] if money else None
        # How rent gets paid (D-114): in time or late, by which method, and on which days of the month.
        timely = patterns.timeliness(board.scope, board.month, timezone.localdate())
        methods = patterns.methods_by_month(board.scope, board.month)
        pay_days = units.pay_days(board.scope, metrics.add_months(board.month, 1 - metrics.TREND_MONTHS),
                                  metrics.month_end(board.month))
        specs = {"timely": patterns.timeliness_chart(timely),
                 "methods": patterns.methods_chart(methods, board.currency) if methods.methods else None,
                 "pay_days": patterns.pay_days_chart(pay_days, board.currency) if pay_days else None}
        on_time = sum(r.counts[units.PAID] for r in timely)
        now_due = sum(r.billed - r.counts[units.DUE] for r in timely)
        return render(request, self.template_name, {
            "board": board, "form": form, "chart": chart, "payments_query": payments_query, "history": history,
            "arrears_change": arrears_change, "compare": compare, "money": money, "money_month": money_month,
            "spark": spark, "highlights": insights.highlights(board, money),
            "timely": timely, "timely_share": metrics.rate(on_time, now_due), "methods": methods, "pay_days": pay_days,
            "specs": specs, "state_labels": [(key, units.STATE_LABELS[key]) for key in patterns.STATES],
            "history_rebuilt": any(p.rebuilt for p in history),
            "can_export": can(m, "reports.export"), "can_units": can(m, "units.view"),
            "can_payments": can(m, "payments.view"), "can_arrears": can(m, "invoices.view"),
            "can_income": can(m, "reports.view_financial"), "can_inbox": can(m, "mpesa.match"),
            "query": request.GET.urlencode(),
        })


    @staticmethod
    def breakdowns(board) -> dict:
        """The figures the charts below the rent trend draw (D-113); the same numbers are in the page's tables."""
        o = board.occupancy
        free = max(o.vacant - o.reserved - o.maintenance, 0)
        return {
            "rate": [None if row.rate is None else round(float(row.rate) * 100, 1) for row in board.trend],
            "occupancy": {
                "labels": [row.label for row in board.occupancy_trend],
                "rate": [None if row.rate is None else round(float(row.rate) * 100, 1)
                         for row in board.occupancy_trend],
                "now": [[_("Occupied"), o.occupied], [_("Empty"), free], [_("Reserved"), o.reserved],
                        [_("In repair"), o.maintenance]],
                "places": [[row.label, row.occupied, row.vacant] for row in board.by_property],
            },
            "collection": [[row.label, float(row.collected), float(max(row.outstanding, 0))]
                           for row in board.collection],
            "methods": [[row.label, float(row.amount), row.count] for row in board.methods],
            "grades": [[grade, n] for grade, n in board.grade_mix],
            "names": {"expected": _("Rent expected"), "collected": _("Rent collected"), "cash": _("Cash received"),
                      "arrears": _("Arrears"), "rate": _("Collection rate"), "occupancy": _("Occupancy"),
                      "outstanding": _("Still to collect"), "occupied": _("Occupied"), "vacant": _("Empty"),
                      "tenants": _("Tenants"), "units": _("units")},
        }


class DashboardExportView(CapabilityRequiredMixin, View):
    required_capability = "reports.export"

    def get(self, request):
        if not can(request.membership, "dashboard.view_financial"):
            raise PermissionDenied("dashboard.view_financial")
        _form, board = _dashboard(request)
        response = _csv_response(f"dashboard-{board.month:%Y-%m}.csv")
        writer = csv.writer(response)
        scope = board.scope.selected_property.name if board.scope.selected_property else _("All your properties")
        writer.writerow([_("Scope"), scope])
        writer.writerow([_("Figures as recorded on"), timezone.localdate().isoformat()])
        writer.writerow([])
        writer.writerow(["Month", "Rent expected", "Rent collected", "Collection rate %", "Cash received"])
        for row in board.trend:
            writer.writerow([row.month.strftime("%Y-%m"), row.expected, row.collected, _pct(row.rate), row.cash])
        writer.writerow([])
        writer.writerow([f"Occupancy on {board.day.isoformat()}"])
        writer.writerow(["Property", "Rentable units", "Occupied", "Vacant", "Reserved", "Under maintenance",
                         "Occupancy %"])
        for row in [*board.by_property, board.occupancy]:
            writer.writerow([row.label or _("Total"), row.rentable, row.occupied, row.vacant, row.reserved,
                             row.maintenance, _pct(row.rate)])
        writer.writerow([])
        writer.writerow([f"Arrears on {timezone.localdate().isoformat()}"])
        writer.writerow(["Days overdue", "Amount"])
        for _key, label, amount in board.arrears.buckets:
            writer.writerow([str(label), amount])
        writer.writerow([_("Total"), board.arrears.total])
        writer.writerow([])
        writer.writerow([f"Collection by property, {board.month:%Y-%m}"])
        writer.writerow(["Property", "Rent expected", "Rent collected", "Still to collect", "Collection rate %"])
        for row in board.collection:
            writer.writerow([row.label, row.expected, row.collected, row.outstanding, _pct(row.rate)])
        writer.writerow([])
        writer.writerow([f"Cash received by method, {board.month:%Y-%m}"])
        writer.writerow(["Method", "Payments", "Amount"])
        for row in board.methods:
            writer.writerow([row.label, row.count, row.amount])
        writer.writerow([])
        writer.writerow([f"Empty units on {board.day.isoformat()}"])
        writer.writerow(["Units", "Asking rent a month", "Units with no asking rent"])
        writer.writerow([board.vacancy.units, board.vacancy.amount, board.vacancy.unpriced])
        writer.writerow([])
        writer.writerow(["Arrears at month end (daily snapshots)"])
        writer.writerow(["Month", "Snapshot day", "1-30 days", "31-60 days", "61-90 days", "Over 90 days", "Total",
                         "Leases overdue", "Rebuilt from the ledger"])
        for p in snapshots.history(board.scope, board.month):
            if p.day is None:
                writer.writerow([p.month.strftime("%Y-%m"), "", "", "", "", "", "", "", ""])
            else:
                writer.writerow([p.month.strftime("%Y-%m"), p.day.isoformat(), p.d1_30, p.d31_60, p.d61_90, p.d90,
                                 p.total, p.leases, "yes" if p.rebuilt else "no"])
        return response


# ---------------------------------------------------------------------------
# Income pack (D-050)
# ---------------------------------------------------------------------------


def _year(request, years: list[int]) -> int:
    try:
        year = int(request.GET.get("year") or years[0])
    except ValueError:
        return years[0]
    return year if year in years else years[0]


class IncomeView(CapabilityRequiredMixin, View):
    template_name = "reports/income.html"
    required_capability = "reports.view_financial"

    def get(self, request):
        m = request.membership
        years = income.years(m)
        pack = income.income_pack(m, _year(request, years))
        return render(request, self.template_name, {
            "pack": pack, "years": years, "specs": _income_charts(pack), "can_export": can(m, "reports.export"),
            "can_settings": can(m, "organization.manage"), "csv_kinds": CSV_KINDS,
            "form": forms.TaxResidenceForm(initial={"landlord_tax_residence": m.organization.landlord_tax_residence}),
        })


def _income_charts(pack) -> dict | None:
    """What came in each month (rent, and the rest: other charges, deposits, not yet applied) against the rent
    billed, and rent by property (D-114). None when nothing was billed or received in the year."""
    if not (pack.total.received or pack.total.rent_billed or pack.total.other_billed):
        return None
    cur = pack.currency
    specs = {"months": charts.chart("bar", [f"{r.key:%b}" for r in pack.months], [
        charts.series(_("Rent received"), [r.rent_received for r in pack.months], "1"),
        charts.series(_("Other money received"), [r.received - r.rent_received for r in pack.months], "2"),
        charts.series(_("Rent billed"), [r.rent_billed for r in pack.months], "4", type="line"),
    ], stacked=True, currency=cur, total=_("Received"))}
    places = sorted((r for r in pack.properties if r.rent_received or r.rent_billed),
                    key=lambda r: -r.rent_received)
    if len(places) > 1:
        specs["places"] = charts.chart("bar", [r.label for r in places], [
            charts.series(_("Rent billed"), [r.rent_billed for r in places], "1-soft"),
            charts.series(_("Rent received"), [r.rent_received for r in places], "1"),
        ], horizontal=True, currency=cur)
    return specs


class TaxResidenceView(CapabilityRequiredMixin, View):
    required_capability = "organization.manage"

    def post(self, request):
        form = forms.TaxResidenceForm(request.POST)
        if form.is_valid():
            try:
                income.set_tax_residence(request.membership, form.cleaned_data["landlord_tax_residence"],
                                         request=request)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            else:
                messages.success(request, _("Tax residence saved."))
        else:
            messages.error(request, _("Choose resident or non-resident."))
        url = reverse("reports:income")
        year = request.POST.get("year", "")
        return redirect(f"{url}?year={year}" if year.isdigit() else url)


class IncomeExportView(CapabilityRequiredMixin, View):
    required_capability = "reports.export"

    def get(self, request, year, kind):
        m = request.membership
        if not can(m, "reports.view_financial"):
            raise PermissionDenied("reports.view_financial")
        if year not in income.years(m) or kind not in (*CSV_KINDS, "pdf"):
            raise Http404
        pack = income.income_pack(m, year)
        if kind == "pdf":
            response = HttpResponse(render_pack(pack), content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="rental-income-{year}.pdf"'
            return response
        response = _csv_response(f"rental-income-{year}-{kind}.csv")
        writer = csv.writer(response)
        for row in getattr(self, f"_{kind}")(pack):
            writer.writerow(row)
        writer.writerow([])
        writer.writerow([str(income.DISCLAIMER)])
        return response

    MONEY_HEADER = ["Rent billed", "Other charges billed", "Received", "Rent received", "Other charges received",
                    "Deposits received", "Not yet applied", "Taxable rent"]

    @staticmethod
    def _money(row):
        return [row.rent_billed, row.other_billed, row.received, row.rent_received, row.other_received,
                row.deposit_received, row.unapplied, row.taxable]

    def _months(self, pack):
        yield ["Month", *self.MONEY_HEADER, "Rate", "Tax estimate"]
        for row in [*pack.months, pack.total]:
            month = row.key.strftime("%Y-%m") if row.key else row.label
            yield [month, *self._money(row), "" if row.key is None else row.rate_label,
                   "" if row.tax is None else row.tax]

    def _properties(self, pack):
        yield ["Property", *self.MONEY_HEADER]
        for row in [*pack.properties, pack.total]:
            yield [row.label, *self._money(row)]

    def _receipts(self, pack):
        yield ["Date paid", "Receipt", "Tenant", "Property", "Unit", "Method", "Reference", "Amount", "Rent",
               "Other charges", "Deposit", "Not yet applied"]
        for part in pack.receipts:
            p = part.payment
            receipt = getattr(p, "receipt", None)
            yield [p.paid_at.isoformat(), receipt.number if receipt else "", p.tenant.name if p.tenant else "",
                   p.lease.unit.property.name, p.lease.unit.code, p.get_method_display(), p.reference, p.amount,
                   part.rent, part.other, part.deposit, part.unapplied]


# ---------------------------------------------------------------------------
# Owner statement (D-053)
# ---------------------------------------------------------------------------


def _statement(request):
    m = request.membership
    owners = statements.owner_choices(m)
    form = forms.OwnerStatementForm(request.GET or None, owners=owners)
    owner = form.value("owner") or (owners[0][0] if owners else None)
    month = form.value("month")
    today = timezone.localdate()
    if month is None or month > today:
        month = statements.default_month(today)
    st = statements.statement(m, owner, month) if owner else None
    if owners and request.GET.get("owner") and st is None:
        raise Http404
    if st is not None:
        form = forms.OwnerStatementForm({"owner": owner, "month": f"{st.month:%Y-%m}"}, owners=owners)
    return form, st


def _statement_url(owner_key, month) -> str:
    return f"{reverse('reports:owner_statement')}?owner={owner_key}&month={month:%Y-%m}"


class OwnerStatementView(CapabilityRequiredMixin, View):
    template_name = "reports/owner_statement.html"
    required_capability = "reports.view_financial"

    def get(self, request):
        form, st = _statement(request)
        m = request.membership
        query = f"owner={st.owner.public_id if st.owner else statements.NO_OWNER}&month={st.month:%Y-%m}" if st else ""
        context = {
            "form": form, "st": st, "query": query, "can_export": can(m, "reports.export"),
            "can_owners": can(m, "properties.manage"),
        }
        if st is not None and st.shows_remittances:
            context.update({
                "can_remit": can(m, "owners.remit"),
                "remit_form": forms.RemittanceForm(initial={
                    "paid_on": timezone.localdate(), "amount": st.remaining if st.remaining > 0 else None}),
                "send_blocker": owners.send_blocker(m, st),
                "can_send_statement": can(m, "owners.send_statement"),
                "send_form": forms.SendStatementForm(initial={"sms": not st.owner.email}),
                "sends": owners.visible_sends(m, st.owner, st.month),
            })
        return render(request, self.template_name, context)


class OwnerStatementPdfView(CapabilityRequiredMixin, View):
    required_capability = "reports.export"

    def get(self, request):
        if not can(request.membership, "reports.view_financial"):
            raise PermissionDenied("reports.view_financial")
        _form, st = _statement(request)
        if st is None:
            raise Http404
        response = HttpResponse(render_statement(st), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{owners.statement_filename(st)}"'
        return response


# ---------------------------------------------------------------------------
# Payments to owners and sending the statement (D-058)
# ---------------------------------------------------------------------------


def _posted_statement(request):
    """The owner and month a form on the statement page was posted for."""
    owner = owners.visible_owner(request.membership, request.POST.get("owner", ""))
    if owner is None:
        raise Http404
    form = forms.OwnerStatementForm(request.POST, owners=[(str(owner.public_id), owner.name)])
    month = form.value("month")
    if month is None:
        raise Http404
    return owner, metrics.month_start(month)


class RemittanceCreateView(CapabilityRequiredMixin, View):
    required_capability = "owners.remit"

    def post(self, request):
        owner, month = _posted_statement(request)
        form = forms.RemittanceForm(request.POST)
        if form.is_valid():
            try:
                owners.record_remittance(request.membership, owner, month=month, request=request, **form.cleaned_data)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
            else:
                messages.success(request, _("Payment to the owner recorded."))
        else:
            messages.error(request, " ".join(e for errors in form.errors.values() for e in errors))
        return redirect(_statement_url(owner.public_id, month))


class RemittanceVoidView(CapabilityRequiredMixin, View):
    required_capability = "owners.remit"

    def post(self, request, public_id):
        m = request.membership
        remittance = OwnerRemittance.objects.filter(organization=m.organization, public_id=public_id).first()
        if remittance is None or owners.visible_owner(m, remittance.owner.public_id) is None:
            raise Http404
        try:
            owners.void_remittance(m, remittance, request.POST.get("reason", ""), request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Payment voided."))
        if request.POST.get("back") == "account":
            return redirect("reports:owner_account", remittance.owner.public_id)
        return redirect(_statement_url(remittance.owner.public_id, remittance.month))


class StatementSendView(CapabilityRequiredMixin, View):
    required_capability = "owners.send_statement"

    def post(self, request):
        m = request.membership
        owner, month = _posted_statement(request)
        st = statements.statement(m, str(owner.public_id), month)
        if st is None:
            raise Http404
        form = forms.SendStatementForm(request.POST)
        form.is_valid()
        try:
            send = owners.send_statement(m, st, sms=form.cleaned_data.get("sms", False), request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            if send.email_status == send.EmailStatus.FAILED:
                messages.error(request, _("Statement %(n)s saved, but the email failed: %(e)s")
                               % {"n": send.number, "e": send.email_error})
            else:
                messages.success(request, _("Statement %(n)s sent.") % {"n": send.number})
        return redirect(_statement_url(owner.public_id, month))


class StatementSendPdfView(CapabilityRequiredMixin, View):
    """The PDF exactly as it was sent. Never public."""

    required_capability = "reports.view_financial"

    def get(self, request, public_id):
        m = request.membership
        send = OwnerStatementSend.objects.filter(organization=m.organization, public_id=public_id).first()
        if send is None or owners.visible_owner(m, send.owner.public_id) is None:
            raise Http404
        try:
            handle = send.pdf.open("rb")
        except FileNotFoundError:
            raise Http404 from None
        response = FileResponse(handle, content_type="application/pdf", filename=f"{send.number}.pdf")
        response["Cache-Control"] = "private, max-age=3600"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response


class OwnerAccountView(CapabilityRequiredMixin, View):
    template_name = "reports/owner_account.html"
    required_capability = "reports.view_financial"

    def get(self, request, public_id):
        m = request.membership
        owner = owners.visible_owner(m, public_id)
        if owner is None:
            raise Http404
        acc = owners.account(m, owner)
        return render(request, self.template_name, {
            "account": acc, "owner": owner, "currency": m.organization.currency,
            "specs": owners.account_specs(acc, m.organization.currency),
            "can_remit": can(m, "owners.remit"), "can_owners": can(m, "properties.manage"),
        })


class FeesView(CapabilityRequiredMixin, View):
    """The agency's own income: management fees over the last 12 finished months (D-127)."""

    template_name = "reports/fees.html"
    required_capability = "reports.view_financial"

    def get(self, request):
        m = request.membership
        rep = fees.report(m)
        if request.GET.get("format") == "csv":
            if not can(m, "reports.export"):
                raise PermissionDenied("reports.export")
            response = _csv_response(f"management-fees-{rep.months[0]:%Y-%m}-to-{rep.months[-1]:%Y-%m}.csv")
            csv.writer(response).writerows(fees.csv_rows(rep))
            return response
        cur = m.organization.currency
        specs = None
        if rep.total.fee_total:
            specs = {"months": charts.chart("bar", [f"{d:%b %Y}" for d in rep.months], [
                charts.series(_("Fees"), rep.total.fees, "1"),
            ], currency=cur)}
            groups = rep.by_branch or rep.by_owner
            specs["split"] = charts.doughnut(
                _("Fees"), charts.fold([(r.label, r.fee_total) for r in groups], _("Others")), charts.SERIES,
                currency=cur)
        return render(request, self.template_name, {
            "report": rep, "currency": cur, "specs": specs, "can_export": can(m, "reports.export"),
            "can_manage_properties": can(m, "properties.manage"),
        })


# ---------------------------------------------------------------------------
# Profit and loss, cash flow, aged receivables (D-065)
# ---------------------------------------------------------------------------


def _finance_filters(request):
    """The chosen property (or None) and the choices, from ?property=<public id>."""
    choices = visible_properties(request.membership, Property.objects.all()).order_by("name")
    value = request.GET.get("property", "").strip()
    prop = None
    if value:
        prop = next((p for p in choices if str(p.public_id) == value), None)
        if prop is None:
            raise Http404
    return prop, choices


class _FinanceView(CapabilityRequiredMixin, View):
    required_capability = "reports.view_financial"
    template_name = ""
    slug = ""

    def csv_rows(self, report):
        raise NotImplementedError

    def charts(self, report) -> dict:
        """The page's charts (D-110, D-114), for static/js/charts.js; the same numbers are in its tables."""
        raise NotImplementedError

    def extra_context(self, report) -> dict:
        return {}

    def build(self, request, prop, *, compare=False):
        today = timezone.localdate()
        start, end = finance.period(request.GET.get("from", ""), request.GET.get("to", ""), today)
        m = request.membership
        return reportcache.cached(m, "money", (start, end, prop and prop.pk, compare),
                                  lambda: finance.money_report(m, start, end, prop, compare=compare))

    def filename(self, report) -> str:
        return f"{self.slug}-{report.start:%Y-%m}-to-{report.end:%Y-%m}.csv"

    def get(self, request):
        m = request.membership
        prop, choices = _finance_filters(request)
        csv_wanted = request.GET.get("format") == "csv"
        report = self.build(request, prop, compare=not csv_wanted)
        if csv_wanted:
            if not can(m, "reports.export"):
                raise PermissionDenied("reports.export")
            response = _csv_response(self.filename(report))
            writer = csv.writer(response)
            writer.writerow([_("Scope"), prop.name if prop else _("All your properties")])
            for row in self.csv_rows(report):
                writer.writerow(row)
            return response
        query = request.GET.copy()
        query["format"] = "csv"
        return render(request, self.template_name, {
            "report": report, "properties": choices, "selected": prop, "can_export": can(m, "reports.export"),
            "csv_query": query.urlencode(), "specs": self.charts(report) if report.months else None,
            **self.extra_context(report),
        })


class ProfitLossView(_FinanceView):
    template_name = "reports/profit_loss.html"
    slug = "profit-and-loss"

    def extra_context(self, report):
        # The expense categories for the doughnut and its key: the largest four, the rest as "Other" (D-114).
        return {"expense_slices": charts.fold(report.expense_categories, _("Other"))}

    def csv_rows(self, report):
        yield ["Month", "Rent collected", "Other charges collected", "Not yet applied", "Income", "Expenses",
               "Net operating income", "Rent billed", "Collection rate %", "Deposits received (not income)",
               "Expenses % of income", "Margin %"]
        for row in [*report.months, report.total]:
            yield [row.key.strftime("%Y-%m") if row.key else row.label, row.rent, row.other, row.unapplied,
                   row.income, row.expenses, row.noi, row.rent_billed, _pct(row.collection_rate), row.deposits,
                   _pct(row.expense_ratio), _pct(row.margin)]
        if report.by_property:
            yield []
            yield ["Property", "Income", "Expenses", "Net operating income", "Margin %", "Collection rate %"]
            for place in report.by_property:
                f = place.figures
                yield [place.property.name, f.income, f.expenses, f.noi, _pct(f.margin), _pct(f.collection_rate)]

    def charts(self, report):
        cur, months = report.currency, report.months
        specs = {"months": charts.chart("bar", [r.label for r in months], [
            charts.series(_("Income"), [r.income for r in months], "1"),
            charts.series(_("Expenses"), [r.expenses for r in months], "2"),
            charts.series(_("Net operating income"), [r.noi for r in months], "3", type="line"),
        ], currency=cur) if len(months) > 1 else None}
        slices = self.extra_context(report)["expense_slices"]
        if slices:
            specs["expenses"] = charts.doughnut(_("Expenses"), slices, charts.SERIES, currency=cur)
        if report.by_property:
            places = report.by_property
            specs["places"] = charts.chart("bar", [p.property.name for p in places], [
                charts.series(_("Net operating income"), [p.figures.noi for p in places], "1", negative="late-4"),
            ], horizontal=True, currency=cur)
        return specs


class CashFlowView(_FinanceView):
    template_name = "reports/cash_flow.html"
    slug = "cash-flow"

    def extra_context(self, report):
        t = report.total
        # (label, amount, chart colour), in the doughnut's order (D-114).
        return {"money_in_parts": [(_("Rent"), t.rent, "1"), (_("Other charges"), t.other, "2"),
                                   (_("Deposits"), t.deposits, "3"), (_("Not yet applied"), t.unapplied, "4")]}

    def csv_rows(self, report):
        yield ["Month", "Rent", "Other charges", "Deposits", "Not yet applied", "Money in", "Expenses",
               "Deposit refunds", "Payments to owners", "Money out", "Net cash"]
        for row in [*report.months, report.total]:
            yield [row.key.strftime("%Y-%m") if row.key else row.label, row.rent, row.other, row.deposits,
                   row.unapplied, row.money_in, row.expenses, row.refunds,
                   row.owner_payments if report.whole_organization else "", row.money_out, row.net]
        if report.by_property:
            yield []
            yield ["Property", "Money in", "Expenses", "Deposit refunds", "Net cash before owner payments"]
            for place in report.by_property:
                f = place.figures
                yield [place.property.name, f.money_in, f.expenses, f.refunds, f.net]

    def charts(self, report):
        cur, months, t = report.currency, report.months, report.total
        specs = {"months": charts.chart("bar", [r.label for r in months], [
            charts.series(_("Money in"), [r.money_in for r in months], "1"),
            charts.series(_("Money out"), [r.money_out for r in months], "2"),
            charts.series(_("Net cash"), [r.net for r in months], "3", type="line"),
        ], currency=cur) if len(months) > 1 else None}
        if t.money_in:
            parts = [(label, amount) for label, amount, _key in self.extra_context(report)["money_in_parts"]]
            specs["money_in"] = charts.doughnut(_("Money in"), parts, charts.SERIES, currency=cur)
        if report.by_property:
            places = report.by_property
            specs["places"] = charts.chart("bar", [p.property.name for p in places], [
                charts.series(_("Net cash"), [p.figures.net for p in places], "1", negative="late-4"),
            ], horizontal=True, currency=cur)
        return specs


class ReceivablesView(CapabilityRequiredMixin, View):
    template_name = "reports/receivables.html"
    required_capability = "reports.view_financial"

    def get(self, request):
        m = request.membership
        prop, choices = _finance_filters(request)
        today = timezone.localdate()
        rows, total = finance.receivables(m, today, prop)
        summary = finance.receivables_summary(rows, total)
        buckets = billing_selectors.AGING_BUCKETS
        if request.GET.get("format") == "csv":
            if not can(m, "reports.export"):
                raise PermissionDenied("reports.export")
            response = _csv_response(f"aged-receivables-{today.isoformat()}.csv")
            writer = csv.writer(response)
            writer.writerow([_("Scope"), prop.name if prop else _("All your properties")])
            writer.writerow([_("As of"), today.isoformat()])
            writer.writerow(["Property", "Lease", "Tenant", *[str(label) for _k, label, *_r in buckets], "Total"])
            for row in rows:
                writer.writerow([row.property.name, "", "", *[row.buckets[k] for k, *_r in buckets], row.total])
                for lease_row in row.leases:
                    lease = lease_row.lease
                    writer.writerow([row.property.name, lease.number, lease.primary_tenant.name
                                     if lease.primary_tenant else "",
                                     *[lease_row.buckets[k] for k, *_r in buckets], sum(lease_row.buckets.values())])
            writer.writerow([_("Total"), "", "", *[total.buckets[k] for k, *_r in buckets], total.total])
            writer.writerow([_("Share of total"), "", "",
                             *[_pct(share) for _k, _l, _a, share in summary.shares], ""])
            return response
        query = request.GET.copy()
        query["format"] = "csv"
        return render(request, self.template_name, {
            "rows": rows, "total": total, "summary": summary,
            "buckets": [(k, label) for k, label, *_r in buckets], "today": today,
            "properties": choices, "selected": prop, "can_export": can(m, "reports.export"),
            "can_arrears": can(m, "invoices.view"), "csv_query": query.urlencode(),
            "currency": m.organization.currency,
            "specs": _receivables_charts(rows, total, summary, m.organization.currency) if rows else None,
        })


def _receivables_charts(rows, total, summary, currency) -> dict:
    """A ring of what is owed by how late it is, and each property's balance split the same way."""
    shares = [(key, label, amount) for key, label, amount, _share in summary.shares]
    specs = {"aging": charts.doughnut(_("Owed"), [(label, amount) for _k, label, amount in shares],
                                      [finance.AGING_CHART_COLORS[k] for k, *_r in shares], currency=currency)}
    if len(rows) > 1:
        specs["places"] = charts.chart(
            "bar", [r.property.name if r.property else _("No property") for r in rows],
            [charts.series(label, [r.buckets[key] for r in rows], finance.AGING_CHART_COLORS[key])
             for key, label, _a in shares],
            stacked=True, horizontal=True, currency=currency, total=_("Owed"))
    return specs


# ---------------------------------------------------------------------------
# Unit by unit (D-112)
# ---------------------------------------------------------------------------


class UnitsView(CapabilityRequiredMixin, View):
    template_name = "reports/units.html"
    required_capability = "dashboard.view_financial"

    def get(self, request):
        m = request.membership
        prop, choices = _finance_filters(request)
        report = reportcache.cached(m, "units", (prop and prop.pk,), lambda: units.unit_report(m, property=prop))
        names = can(m, "tenants.view")
        if request.GET.get("format") == "csv":
            if not can(m, "reports.export"):
                raise PermissionDenied("reports.export")
            return self.csv(report, names)
        query = request.GET.copy()
        query["format"] = "csv"
        return render(request, self.template_name, {
            "report": report, "s": report.summary, "properties": choices, "selected": prop,
            "states": units.STATES, "show_names": names, "can_units": can(m, "units.view"),
            "can_leases": can(m, "leases.view"), "can_export": can(m, "reports.export"),
            "csv_query": query.urlencode(), **self.charts(report),
        })

    @staticmethod
    def charts(report) -> dict:
        """D-114: the 12 months of rent paid in time or late across the units, and each kind of unit compared."""
        if not report.rows:
            return {"timely": None, "specs": None}
        timely = patterns.timeliness_from_cells(report.months, (row.cells for row in report.rows))
        specs = {"timely": patterns.timeliness_chart(timely)}
        if len(report.by_type) > 1:
            specs["kinds"] = patterns.kinds_chart(report.by_type)
        if report.pay_days:
            specs["pay_days"] = patterns.pay_days_chart(report.pay_days, report.currency)
        return {"timely": timely, "specs": specs,
                "state_labels": [(key, units.STATE_LABELS[key]) for key in patterns.STATES]}

    @staticmethod
    def csv(report, names: bool) -> HttpResponse:
        response = _csv_response(f"unit-by-unit-{report.day.isoformat()}.csv")
        writer = csv.writer(response)
        sel = report.scope.selected_property
        writer.writerow([_("Scope"), sel.name if sel else _("All your properties")])
        writer.writerow([_("As of"), report.day.isoformat()])
        writer.writerow([])
        head = ["Property", "Unit", "Type", "Status", "Tenant", "Rent a month", "Rent since", "Asking rent",
                "Under asking by", *[m.strftime("%Y-%m") for m in report.months],
                "Rent expected 12 months", "Rent collected 12 months", "Collection rate %",
                "Days empty", "Days counted", "Empty days at asking rent", "Grade", "Ends or moves out"]
        if report.shows_arrears:
            head += ["Overdue", "Days late"]
        writer.writerow(head)
        for row in report.rows:
            tenant = row.lease.primary_tenant if names and row.lease else None
            line = [row.unit.property.name, row.unit.code, units.type_label(row.unit), row.status,
                    tenant.name if tenant else "", row.rent or "", row.rent_since or "", row.unit.list_rent or "",
                    row.below_asking or "", *[c.state for c in row.cells], row.expected, row.collected,
                    _pct(row.rate), row.empty_days, row.days_in_window, row.empty_loss or "",
                    row.grade.label if row.grade else "", row.ends_on or ""]
            if report.shows_arrears:
                line += [row.overdue, row.days_late]
            writer.writerow(line)
        writer.writerow([])
        writer.writerow(["By kind of unit"])
        writer.writerow(["Kind", "Units", "Occupied", "Occupancy %", "Average rent", "Average asking rent",
                         "Rent expected 12 months", "Rent collected 12 months", "Collection rate %",
                         "Empty % of the time"])
        for t in report.by_type:
            writer.writerow([t.label, t.units, t.occupied, _pct(t.occupancy), t.average_rent or "",
                             t.average_asking or "", t.expected, t.collected, _pct(t.rate), _pct(t.empty_share)])
        writer.writerow([])
        writer.writerow(["Payments by day of the month, last 12 months"])
        writer.writerow(["Days", "Payments", "Amount", "Share %"])
        for p in report.pay_days:
            writer.writerow([p.label, p.count, p.amount, _pct(p.share)])
        return response


# ---------------------------------------------------------------------------
# Accounting export (D-075)
# ---------------------------------------------------------------------------


class AccountingExportView(CapabilityRequiredMixin, View):
    template_name = "reports/accounting_export.html"
    required_capability = "reports.export"

    def get(self, request):
        m = request.membership
        prop, choices = _finance_filters(request)
        start, end = finance.period(request.GET.get("from", ""), request.GET.get("to", ""), timezone.localdate())
        export = accounting.build(m, start, end, prop)
        if request.GET.get("download"):
            audit.record("reports.accounting_export", actor=request.user, organization=m.organization,
                         request=request, changes={"months": [None, export.label],
                                                   "property": [None, prop.name if prop else "all"]})
            response = HttpResponse(accounting.to_zip(export), content_type="application/zip")
            response["Content-Disposition"] = f'attachment; filename="{export.filename}"'
            return response
        query = request.GET.copy()
        query["from"], query["to"] = f"{start:%Y-%m}", f"{end:%Y-%m}"
        query["download"] = "1"
        return render(request, self.template_name, {
            "export": export, "report": export, "properties": choices, "selected": prop,
            "download_query": query.urlencode(), "accounts": export.accounts(), "can_export": True,
        })
