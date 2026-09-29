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
from properties.models import Property

from . import forms, income, metrics, owners, statements
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
    return form, metrics.dashboard(m, month=month, property=form.value("property"), today=today)


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
        m = request.membership
        return render(request, self.template_name, {
            "board": board, "form": form, "chart": chart, "payments_query": payments_query,
            "can_export": can(m, "reports.export"), "can_units": can(m, "units.view"),
            "can_payments": can(m, "payments.view"), "can_arrears": can(m, "invoices.view"),
            "can_income": can(m, "reports.view_financial"), "can_inbox": can(m, "mpesa.match"),
            "query": request.GET.urlencode(),
        })


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
            "pack": pack, "years": years, "can_export": can(m, "reports.export"),
            "can_settings": can(m, "organization.manage"), "csv_kinds": CSV_KINDS,
            "form": forms.TaxResidenceForm(initial={"landlord_tax_residence": m.organization.landlord_tax_residence}),
        })


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
        return render(request, self.template_name, {
            "account": owners.account(m, owner), "owner": owner, "currency": m.organization.currency,
            "can_remit": can(m, "owners.remit"), "can_owners": can(m, "properties.manage"),
        })
