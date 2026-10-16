"""Charge types, invoices, arrears and a lease's account. Thin views; services do the work (D-009).

Anything outside the member's properties is a 404, never a 403.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can, visible_properties
from core.ids import uuid_or_none
from leases import paperwork
from leases.models import Lease, LeaseTenant
from leases.services import visible_leases
from meters import readiness
from payments import selectors as payment_selectors
from payments import services as payment_services
from properties.views import _apply_errors
from reports import metrics, patterns

from . import deposits, followups, forms, invoicing, late_fees, selectors, services
from .models import ChargeType, DepositEntry, Invoice, LedgerEntry

PAGE_SIZE = 25


class ChargeTypeListView(CapabilityRequiredMixin, View):
    template_name = "billing/charge_types.html"
    required_capability = "charges.manage"

    def context(self, request, form=None):
        org = request.organization
        services.ensure_default_charge_types(org)
        return {
            "charge_types": ChargeType.objects.for_org(org),
            "archived": ChargeType.all_objects.for_org(org).archived(),
            "form": form or forms.ChargeTypeForm(),
        }

    def get(self, request):
        return render(request, self.template_name, self.context(request))

    def post(self, request):
        m = request.membership
        action = request.POST.get("action", "create")
        if action == "create":
            form = forms.ChargeTypeForm(request.POST)
            if form.is_valid():
                try:
                    services.create_charge_type(m, request=request, **form.cleaned_data)
                except ValidationError as exc:
                    _apply_errors(form, exc)
                else:
                    messages.success(request, _("Charge type added."))
                    return redirect("billing:charge_types")
            return render(request, self.template_name, self.context(request, form=form))
        ct = get_object_or_404(ChargeType.all_objects.for_org(request.organization),
                               public_id=uuid_or_none(request.POST.get("charge_type")))
        try:
            if action == "rename":
                services.rename_charge_type(m, ct, name=request.POST.get("name", ""), request=request)
                messages.success(request, _("Charge type renamed."))
            elif action == "archive":
                services.archive_charge_type(m, ct, request=request)
                messages.success(request, _("Charge type archived."))
            elif action == "restore":
                services.restore_charge_type(m, ct, request=request)
                messages.success(request, _("Charge type restored."))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("billing:charge_types")


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


def _get_invoice(request, public_id) -> Invoice:
    return get_object_or_404(selectors.visible_invoices(request.membership), public_id=public_id)


class InvoicePdfView(CapabilityRequiredMixin, View):
    required_capability = "invoices.view"

    def get(self, request, public_id):
        from .pdf import render_invoice

        invoice = _get_invoice(request, public_id)
        response = HttpResponse(render_invoice(invoice), content_type="application/pdf")
        name = invoice.number or "draft-invoice"
        response["Content-Disposition"] = f'inline; filename="{name}.pdf"'
        response["Cache-Control"] = "private, no-store"
        return response


def _get_lease(request, public_id) -> Lease:
    if not can(request.membership, "invoices.view"):
        raise PermissionDenied("invoices.view")
    qs = visible_leases(request.membership, Lease.all_objects.exclude(status=Lease.Status.DRAFT))
    return get_object_or_404(qs.select_related("unit__property"), public_id=public_id)


class InvoiceListView(CapabilityRequiredMixin, View):
    template_name = "billing/invoice_list.html"
    required_capability = "invoices.view"

    def get(self, request):
        today = timezone.localdate()
        q = request.GET.get("q", "").strip()[:60]
        status = request.GET.get("status", "")
        overdue = request.GET.get("overdue") == "1"
        qs = selectors.filter_invoices(selectors.visible_invoices(request.membership), status=status, q=q,
                                       overdue=overdue, today=today)
        qs = qs.prefetch_related(Prefetch("lease__lease_tenants", LeaseTenant.objects.select_related("tenant")))
        page = Paginator(qs.order_by("-issue_date", "-pk"), PAGE_SIZE).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "page": page, "q": q, "status": status, "overdue": overdue, "today": today,
            "totals": selectors.invoice_totals(qs, today), "currency": request.membership.organization.currency,
            "filtered": bool(q or status or overdue),
            "status_filters": [(k, label) for k, (label, _cond) in selectors.STATUS_FILTERS.items()],
            "can_generate": can(request.membership, "invoices.generate"),
        })


class InvoiceDetailView(CapabilityRequiredMixin, View):
    template_name = "billing/invoice_detail.html"
    required_capability = "invoices.view"

    def context(self, request, invoice, void_form=None):
        can_void = invoicing.can_void(request.membership, invoice)
        return {
            "invoice": invoice, "lease": invoice.lease, "today": timezone.localdate(),
            "lines": invoice.lines.select_related("charge_type"),
            "entries": LedgerEntry.objects.filter(invoice=invoice).order_by("entry_date", "pk"),
            "can_void": can_void, "void_form": (void_form or forms.VoidForm()) if can_void else None,
            # Send it to the tenant in words staff choose (D-125).
            "can_send": invoice.status not in (Invoice.Status.DRAFT, Invoice.Status.VOID)
            and can(request.membership, "messages.send", invoice.lease.unit.property),
        }

    def get(self, request, public_id):
        return render(request, self.template_name, self.context(request, _get_invoice(request, public_id)))

    def post(self, request, public_id):
        invoice = _get_invoice(request, public_id)
        form = forms.VoidForm(request.POST)
        if form.is_valid():
            try:
                invoicing.void_invoice(request.membership, invoice, reason=form.cleaned_data["reason"],
                                       request=request)
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Invoice voided. The month can be billed again."))
                return redirect("billing:invoice", public_id=invoice.public_id)
        return render(request, self.template_name, self.context(request, invoice, void_form=form))


class GenerateView(CapabilityRequiredMixin, View):
    """Bills a month by hand. The daily job does this on its own; this is for catching up or checking."""

    template_name = "billing/generate.html"
    required_capability = "invoices.generate"

    def context(self, request, form=None, result=None, month=None):
        today = timezone.localdate()
        due = invoicing.months_to_bill(request.organization, today)
        shown = due + [month] if month and month not in due else due
        return {
            "form": form or forms.GenerateForm(initial={"month": today.strftime("%Y-%m")}),
            "result": result, "month": month, "due_months": due,
            "meter_checks": ([readiness.check(request.membership, m) for m in shown]
                             if can(request.membership, "meters.view") else []),
        }

    def get(self, request):
        return render(request, self.template_name, self.context(request))

    def post(self, request):
        form = forms.GenerateForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, self.context(request, form))
        month = form.cleaned_data["month"]
        if month > invoicing.next_month(timezone.localdate()):
            form.add_error("month", _("You can bill up to next month."))
            return render(request, self.template_name, self.context(request, form))
        result = invoicing.generate_month(request.organization, month, actor=request.membership, request=request)
        return render(request, self.template_name, self.context(request, form, result=result, month=month))


class ArrearsView(CapabilityRequiredMixin, View):
    template_name = "billing/arrears.html"
    required_capability = "invoices.view"

    def get(self, request):
        today = timezone.localdate()
        show_all = request.GET.get("all") == "1"
        rows = selectors.arrears(request.membership, today, overdue_only=not show_all)
        return render(request, self.template_name, {
            "rows": rows, "totals": selectors.aging_totals(rows), "buckets": selectors.AGING_BUCKETS,
            "today": today, "show_all": show_all, "summary": selectors.arrears_summary(rows, today),
            "currency": request.membership.organization.currency,
            "can_late_fees": can(request.membership, "invoices.generate"),
        })


class LateFeeListView(CapabilityRequiredMixin, View):
    """Late fees waiting to be charged or skipped, and the ones decided lately (D-129)."""

    template_name = "billing/late_fees.html"
    required_capability = "invoices.generate"

    def get(self, request):
        m = request.membership
        pending = list(late_fees.to_approve(m).prefetch_related(
            Prefetch("lease__lease_tenants", LeaseTenant.objects.select_related("tenant"))))
        decided = (late_fees.visible_fees(m).exclude(status="PENDING")
                   .select_related("invoice", "fee_invoice", "lease__unit__property", "decided_by")
                   .order_by("-decided_at", "-pk")[:PAGE_SIZE])
        org = request.organization
        return render(request, self.template_name, {
            "pending": [(fee, late_fees.fee_for(org, fee.invoice.outstanding)) for fee in pending],
            "decided": decided, "rule": late_fees.describe_rule(org), "is_on": org.late_fee_mode != "OFF",
            "can_settings": can(m, "organization.manage"), "currency": org.currency,
        })

    def post(self, request):
        m = request.membership
        ids = [uid for uid in map(uuid_or_none, request.POST.getlist("fee")) if uid]
        fees = list(late_fees.to_approve(m).filter(public_id__in=ids))
        if not fees:
            messages.error(request, _("Tick at least one fee."))
            return redirect("billing:late_fees")
        charge = request.POST.get("action") == "charge"
        batch = late_fees.decide_many(m, fees, charge=charge, reason=request.POST.get("reason", "")[:300],
                                      request=request)
        if batch.charged:
            messages.success(request, ngettext("%(n)s late fee charged.", "%(n)s late fees charged.",
                                               len(batch.charged)) % {"n": len(batch.charged)})
        if batch.skipped:
            messages.info(request, ngettext("%(n)s late fee skipped.", "%(n)s late fees skipped.",
                                            batch.skipped) % {"n": batch.skipped})
        for error in batch.errors:
            messages.error(request, error)
        return redirect("billing:late_fees")


class CallListView(CapabilityRequiredMixin, View):
    """Who to call today about money overdue (D-052)."""

    template_name = "billing/call_list.html"
    required_capability = "invoices.view"

    def get(self, request, form=None, follow_up_lease=None):
        m = request.membership
        today = timezone.localdate()
        filters = forms.CallListFilterForm(request.GET or None, properties=visible_properties(m))
        board = followups.call_list(m, today, property=filters.value("property"), grade=filters.value("grade") or "")
        # Only "to call" can run long; promises and today's calls stay whole.
        to_call = Paginator(board.to_call, PAGE_SIZE).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "board": board, "to_call": to_call, "page": to_call, "filters": filters, "today": today,
            "form": form or forms.FollowUpForm(),
            "follow_up_lease": follow_up_lease, "can_follow_up": can(m, "arrears.follow_up"),
            "can_tenants": can(m, "tenants.view"), "query": request.GET.urlencode(),
        })


class FollowUpView(CapabilityRequiredMixin, View):
    required_capability = "arrears.follow_up"

    def post(self, request, public_id):
        lease = _get_lease(request, public_id)
        form = forms.FollowUpForm(request.POST)
        if form.is_valid():
            try:
                followups.record_follow_up(request.membership, lease, request=request, **form.cleaned_data)
            except ValidationError as e:
                _apply_errors(form, e)
            else:
                messages.success(request, _("Saved for %(lease)s.") % {
                    "lease": getattr(lease.primary_tenant, "name", "") or lease.number})
                query = request.POST.get("next_query", "")
                return redirect(reverse("billing:call_list") + (f"?{query}" if query else ""))
        return CallListView().get(request, form=form, follow_up_lease=lease)


# ---------------------------------------------------------------------------
# A lease's account: statement, opening balance and deposits
# ---------------------------------------------------------------------------


class LeaseAccountView(CapabilityRequiredMixin, View):
    template_name = "billing/lease_account.html"
    required_capability = "invoices.view"
    # action -> (context name, form class, prefix)
    FORMS = {
        "opening_balance": ("opening_form", forms.OpeningBalanceForm, None),
        "deposit_received": ("received_form", forms.DepositForm, "received"),
        "deposit_deduct": ("deduct_form", forms.DeductForm, "deduct"),
        "deposit_refund": ("refund_form", forms.DepositForm, "refund"),
    }

    def context(self, request, lease, **forms_in):
        m, prop, today = request.membership, lease.unit.property, timezone.localdate()
        period = forms.StatementForm(request.GET or None)
        start = end = None
        if period.is_bound and period.is_valid():
            start, end = period.cleaned_data["start"], period.cleaned_data["end"]
        is_live = lease.archived_at is None
        opening = invoicing.opening_balance(lease)
        ctx = {
            "lease": lease, "today": today, "period_form": period,
            "statement": selectors.statement(lease, start=start, end=end),
            "balance": invoicing.lease_balance(lease),
            "clearance": deposits.clearance_statement(lease),
            "move_out": paperwork.move_out_applies(lease),
            "takes_deposits": lease.organization.takes_deposits,
            "reversed_ids": set(DepositEntry.objects.filter(lease=lease, reversal_of__isnull=False)
                                .values_list("reversal_of_id", flat=True)),
            "invoices": Invoice.objects.filter(lease=lease).order_by("-period_start", "-pk")[:12],
            "opening": opening,
            "can_opening": is_live and can(m, "invoices.adjust", prop),
            "can_record": is_live and can(m, "deposits.record", prop),
            "can_deduct": is_live and can(m, "deposits.deduct", prop),
            "payments": payment_selectors.for_lease(lease)[:12] if can(m, "payments.view", prop) else None,
            "credit": payment_services.unallocated_credit(lease),
            "can_record_payment": is_live and can(m, "payments.record", prop),
            "can_apply_credit": can(m, "payments.allocate", prop),
            # D-110: how this tenancy pays, from its grade and its money over the lease's life.
            "record": selectors.payment_record(lease),
            "grade": metrics.grades([lease], today)[lease.pk],
        }
        # D-114: the last 12 months of rent, billed against paid and coloured by whether it was paid in time.
        months = patterns.lease_months(lease, today)
        ctx["months"] = months if months.billed else None
        ctx["months_chart"] = patterns.lease_chart(months, lease.currency) if months.billed else None
        on_day = {"entry_date": today.isoformat()}
        if ctx["can_opening"]:
            ctx["opening_form"] = forms_in.get("opening_form") or forms.OpeningBalanceForm(initial={
                "amount": opening.amount if opening else "",
                "as_of": (opening.entry_date if opening else lease.start_date).isoformat()})
        if ctx["can_record"]:
            ctx["received_form"] = forms_in.get("received_form") or forms.DepositForm(
                initial={**on_day, "amount": lease.deposit_amount or ""}, prefix="received")
            ctx["refund_form"] = forms_in.get("refund_form") or forms.DepositForm(initial=on_day, prefix="refund")
        if ctx["can_deduct"]:
            ctx["deduct_form"] = forms_in.get("deduct_form") or forms.DeductForm(
                initial=on_day, prefix="deduct", organization=lease.organization)
        # D-150: when a refund or statement of deductions falls due, by the owner's rule.
        due = deposits.settle_by(lease)
        if due is not None and ctx["clearance"]["held"] > 0:
            ctx["settle_by"], ctx["settle_days"] = due, (due - today).days
        return ctx

    def get(self, request, public_id):
        lease = _get_lease(request, public_id)
        return render(request, self.template_name, self.context(request, lease))

    def post(self, request, public_id):
        lease = _get_lease(request, public_id)
        action = request.POST.get("action", "")
        if action == "deposit_reverse":
            return self.reverse(request, lease)
        if action not in self.FORMS:
            return redirect("billing:lease_account", public_id=lease.public_id)
        name, form_class, prefix = self.FORMS[action]
        if action == "deposit_deduct":
            form = form_class(request.POST, request.FILES, prefix=prefix, organization=lease.organization)
        else:
            form = form_class(request.POST, prefix=prefix)
        if form.is_valid():
            try:
                message = self.apply(request, lease, action, form.cleaned_data)
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, message)
                return redirect("billing:lease_account", public_id=lease.public_id)
        return render(request, self.template_name, self.context(request, lease, **{name: form}))

    def apply(self, request, lease, action, data) -> str:
        m = request.membership
        if action == "opening_balance":
            invoicing.set_opening_balance(m, lease, amount=data["amount"], as_of=data["as_of"],
                                          reason=data["reason"], request=request)
            return _("Balance brought forward saved.")
        common = {"amount": data["amount"], "deposit_type": data["deposit_type"], "entry_date": data["entry_date"],
                  "request": request}
        if action == "deposit_received":
            deposits.record_received(m, lease, reference=data["reference"], reason=data["reason"], **common)
            return _("Deposit recorded.")
        if action == "deposit_deduct":
            deposits.deduct(m, lease, reason=data["reason"], apply_to_balance=data.get("apply_to_balance", False),
                            heading=data["heading"], evidence=data.get("evidence"), **common)
            return _("Deduction recorded.")
        deposits.refund(m, lease, reference=data["reference"], reason=data["reason"], **common)
        return _("Refund recorded.")

    def reverse(self, request, lease):
        entry_id = request.POST.get("entry", "")
        entry = get_object_or_404(DepositEntry.objects.filter(lease=lease),
                                  pk=int(entry_id) if entry_id.isdigit() else 0)
        form = forms.ReverseDepositForm(request.POST)
        if not form.is_valid():
            messages.error(request, _("Say why this entry is being corrected."))
            return redirect("billing:lease_account", public_id=lease.public_id)
        try:
            deposits.reverse(request.membership, entry, reason=form.cleaned_data["reason"], request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Entry corrected."))
        return redirect("billing:lease_account", public_id=lease.public_id)


class DepositEvidenceView(CapabilityRequiredMixin, View):
    """Serves the receipt, quote or photo behind a deduction, only to members who may see the lease. Never public."""

    required_capability = "invoices.view"

    def get(self, request, public_id, entry_id):
        lease = _get_lease(request, public_id)
        entry = get_object_or_404(DepositEntry.objects.filter(lease=lease).exclude(evidence=""), pk=entry_id)
        try:
            handle = entry.evidence.open("rb")
        except FileNotFoundError:
            raise Http404 from None
        if entry.evidence.name.lower().endswith(".pdf"):
            response = FileResponse(handle, content_type="application/pdf", as_attachment=True,
                                    filename=f"deduction-{entry.pk}-evidence.pdf")
        else:
            response = FileResponse(handle, content_type="image/jpeg")
        response["Cache-Control"] = "private, max-age=3600"
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response
