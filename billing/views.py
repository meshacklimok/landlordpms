"""Charge types, invoices, arrears and a lease's account. Thin views; services do the work (D-009).

Anything outside the member's properties is a 404, never a 403.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can, visible_properties
from leases.models import Lease, LeaseTenant
from leases.services import visible_leases
from payments import selectors as payment_selectors
from payments import services as payment_services
from properties.views import _apply_errors

from . import deposits, followups, forms, invoicing, selectors, services
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
                               public_id=request.POST.get("charge_type"))
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
        return {
            "form": form or forms.GenerateForm(initial={"month": today.strftime("%Y-%m")}),
            "result": result, "month": month, "due_months": invoicing.months_to_bill(request.organization, today),
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
            "today": today, "show_all": show_all,
        })


class CallListView(CapabilityRequiredMixin, View):
    """Who to call today about money overdue (D-052)."""

    template_name = "billing/call_list.html"
    required_capability = "invoices.view"

    def get(self, request, form=None, follow_up_lease=None):
        m = request.membership
        today = timezone.localdate()
        filters = forms.CallListFilterForm(request.GET or None, properties=visible_properties(m))
        board = followups.call_list(m, today, property=filters.value("property"), grade=filters.value("grade") or "")
        return render(request, self.template_name, {
            "board": board, "filters": filters, "today": today, "form": form or forms.FollowUpForm(),
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
        }
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
            ctx["deduct_form"] = forms_in.get("deduct_form") or forms.DeductForm(initial=on_day, prefix="deduct")
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
            deposits.deduct(m, lease, reason=data["reason"], apply_to_balance=data["apply_to_balance"], **common)
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
