"""Payments: list, record, review, confirm/reject/reverse and receipts. Thin views; services do the work.

Anything outside the member's properties is a 404, never a 403.
"""

import csv
import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, Http404, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can, visible_properties
from billing.invoicing import lease_balance
from core.money import ZERO
from leases.models import Lease
from leases.services import visible_leases
from properties.models import Property
from properties.views import _apply_errors

from . import forms, selectors, services
from .models import Payment

PAGE_SIZE = 25
PICKER_SIZE = 20


def _get_payment(request, public_id) -> Payment:
    return get_object_or_404(selectors.visible_payments(request.membership), public_id=public_id)


def _get_live_lease(request, public_id) -> Lease:
    qs = visible_leases(request.membership, Lease.objects.exclude(status=Lease.Status.DRAFT))
    return get_object_or_404(qs.select_related("unit__property", "organization"), public_id=public_id)


def _filtered(request):
    """(filter form, payments matching every filter but status, the same with status applied)."""
    m = request.membership
    form = forms.PaymentFilterForm(request.GET, properties=visible_properties(m, Property.all_objects.all())
                                   .order_by("name"))
    visible = selectors.visible_payments(m)
    base = selectors.filter_payments(visible, **form.without_status())
    return form, base, selectors.filter_payments(base, status=form.filters().get("status", ""))


class PaymentListView(CapabilityRequiredMixin, View):
    template_name = "payments/payment_list.html"
    required_capability = "payments.view"

    def get(self, request):
        m = request.membership
        form, base, qs = _filtered(request)
        filters = form.filters()
        page = Paginator(qs.order_by("-paid_at", "-pk"), PAGE_SIZE).get_page(request.GET.get("page"))
        today = timezone.localdate()
        return render(request, self.template_name, {
            "page": page, "form": form, "filters": filters,
            "status": filters.get("status", ""),
            "filtered": bool(form.without_status()),
            "totals": selectors.totals_by_state(base),
            "states": Payment.STATES,
            "presets": [(key, start, end, start == filters.get("date_from") and end == filters.get("date_to"))
                        for key, start, end in selectors.period_presets(today)],
            "pending_count": selectors.pending_review(m).count(),
            "can_record": can(m, "payments.record"),
            "can_export": can(m, "reports.export"),
            "show_property": form.fields["property"].queryset.count() > 1,
        })


class PaymentExportView(CapabilityRequiredMixin, View):
    """The list, with the same filters, as CSV. Values that a spreadsheet would run as formulas are quoted."""

    required_capability = "reports.export"
    HEADER = ["Date paid", "Status", "Amount", "Currency", "Method", "Reference", "Receipt", "Tenant",
              "Property", "Unit", "Lease", "Received into", "Recorded by", "Recorded at", "Confirmed at",
              "Reversed or rejected at", "Reason"]

    def get(self, request):
        if not can(request.membership, "payments.view"):
            raise PermissionDenied("payments.view")
        _form, _base, qs = _filtered(request)
        qs = qs.select_related("recorded_by").order_by("-paid_at", "-pk")
        writer = csv.writer(_Echo())
        rows = (writer.writerow([_cell(v) for v in row]) for row in self._rows(qs))
        response = StreamingHttpResponse(rows, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="payments-{timezone.localdate():%Y-%m-%d}.csv"'
        return response

    def _rows(self, qs):
        yield ["﻿" + self.HEADER[0], *self.HEADER[1:]]  # BOM so Excel reads UTF-8
        for p in qs.iterator(chunk_size=500):
            receipt = getattr(p, "receipt", None)
            yield [p.paid_at.isoformat(), p.get_state_display(), p.amount, p.lease.currency,
                   p.get_method_display(), p.reference, receipt.number if receipt else "",
                   p.tenant.name if p.tenant else "", p.lease.unit.property.name, p.lease.unit.code, p.lease.number,
                   p.payment_account or "", p.recorded_by or "", _when(p.created_at), _when(p.confirmed_at),
                   _when(p.reversed_at), p.reversal_reason]


class _Echo:
    def write(self, value):
        return value


def _cell(value) -> str:
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _when(value) -> str:
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M") if value else ""


class ReviewQueueView(CapabilityRequiredMixin, View):
    template_name = "payments/review.html"
    required_capability = "payments.confirm"

    def get(self, request):
        payments = list(selectors.with_duplicate_flag(selectors.pending_review(request.membership)))
        return render(request, self.template_name, {
            "payments": payments,
            "totals": selectors.totals_by_state(selectors.pending_review(request.membership))["pending"],
            "today": timezone.localdate(),
        })

    def post(self, request):
        """Confirms the ticked payments, each oldest invoice first. One failure does not stop the rest."""
        ids = [i for i in request.POST.getlist("payment")[:200] if _is_uuid(i)]
        chosen = selectors.pending_review(request.membership).filter(public_id__in=ids) if ids else []
        done, failed = [], []
        for payment in chosen:
            try:
                services.confirm_payment(request.membership, payment, request=request)
            except (ValidationError, PermissionDenied) as exc:
                failed.append(f"{payment.lease.unit.code} {payment.reference or payment.paid_at}: "
                              + " ".join(getattr(exc, "messages", [str(exc)])))
            else:
                done.append(payment)
        if done:
            messages.success(request, ngettext("Confirmed %(n)d payment and issued its receipt.",
                                               "Confirmed %(n)d payments and issued their receipts.",
                                               len(done)) % {"n": len(done)})
        for line in failed:
            messages.error(request, _("Not confirmed: %(detail)s") % {"detail": line})
        if not done and not failed:
            messages.info(request, _("Tick the payments you want to confirm."))
        return redirect("payments:review")


class LeasePickerView(CapabilityRequiredMixin, View):
    """Start recording from the payments page: find the lease, biggest balance first."""

    template_name = "payments/pick_lease.html"
    required_capability = "payments.record"

    def get(self, request):
        q = request.GET.get("q", "").strip()[:60]
        leases = list(selectors.payable_leases(request.membership, q=q)[:PICKER_SIZE + 1])
        return render(request, self.template_name, {
            "q": q, "leases": leases[:PICKER_SIZE], "more": len(leases) > PICKER_SIZE,
        })


class RecordPaymentView(CapabilityRequiredMixin, View):
    template_name = "payments/record.html"
    required_capability = "payments.record"

    def make_form(self, request, lease, data=None):
        m = request.membership
        return forms.PaymentForm(data, lease=lease, can_allocate=can(m, "payments.allocate", lease.unit.property),
                                 visible=selectors.visible_payments(m))

    REFERENCE_HINTS = {
        Payment.Method.MPESA: gettext_lazy("e.g. SGH7K2L9QX"),
        Payment.Method.BANK: gettext_lazy("Bank transaction reference"),
        Payment.Method.CHEQUE: gettext_lazy("Cheque number"),
        Payment.Method.CASH: gettext_lazy("Receipt book number, if any"),
        Payment.Method.OTHER: gettext_lazy("Any reference"),
    }
    METHOD_ICONS = {"MPESA": "bi-phone", "BANK": "bi-bank", "CASH": "bi-cash-coin", "CHEQUE": "bi-receipt",
                    "OTHER": "bi-three-dots"}

    def context(self, request, lease, form):
        m = request.membership
        today = timezone.localdate()
        invoices = list(services.open_invoices(lease))
        for invoice in invoices:
            invoice.overdue = invoice.is_overdue(today)
        return {
            "lease": lease, "balance": lease_balance(lease), "form": form,
            "invoices": invoices, "open_total": sum((i.outstanding for i in invoices), ZERO),
            "recent": selectors.for_lease(lease)[:3] if can(m, "payments.view") else [],
            "will_confirm": can(m, "payments.confirm", lease.unit.property),
            "methods": [(value, label, self.METHOD_ICONS[value]) for value, label in Payment.Method.choices],
            "reference_hints": {k: str(v) for k, v in self.REFERENCE_HINTS.items()},
        }

    def get(self, request, public_id):
        lease = _get_live_lease(request, public_id)
        return render(request, self.template_name, self.context(request, lease, self.make_form(request, lease)))

    def post(self, request, public_id):
        lease = _get_live_lease(request, public_id)
        form = self.make_form(request, lease, request.POST)
        if form.is_valid():
            try:
                payment = services.record_payment(request.membership, lease, request=request, **form.service_kwargs())
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                if payment.status == Payment.Status.CONFIRMED:
                    messages.success(request, _("Payment confirmed. Receipt %(number)s issued.")
                                     % {"number": payment.receipt.number})
                else:
                    messages.success(request, _("Payment recorded. It counts once a manager confirms it."))
                return redirect("payments:detail", public_id=payment.public_id)
        return render(request, self.template_name, self.context(request, lease, form))


class PaymentDetailView(CapabilityRequiredMixin, View):
    template_name = "payments/payment_detail.html"
    required_capability = "payments.view"
    ACTIONS = ("confirm", "reject", "reverse")

    def context(self, request, payment, *, confirm_form=None, reject_form=None, reverse_form=None):
        m, prop = request.membership, payment.lease.unit.property
        pending = payment.status == Payment.Status.PENDING_REVIEW
        can_confirm = pending and can(m, "payments.confirm", prop)
        can_reverse = payment.status == Payment.Status.CONFIRMED and can(m, "payments.reverse", prop)
        allocations = list(payment.allocations.select_related("invoice").order_by("pk"))
        allocated = sum((a.amount for a in allocations), ZERO)
        ctx = {
            "payment": payment, "lease": payment.lease,
            "allocations": allocations, "allocated": allocated, "unallocated": payment.amount - allocated,
            "receipt": getattr(payment, "receipt", None),
            "duplicates": selectors.duplicates_of(payment).filter(
                pk__in=selectors.visible_payments(m).values("pk")).select_related("lease__unit")[:5],
            "can_confirm": can_confirm, "can_reverse": can_reverse,
            "can_record": can(m, "payments.record", prop) and payment.lease.status != Lease.Status.DRAFT
            and payment.lease.archived_at is None,
        }
        if can_confirm:
            ctx["confirm_form"] = confirm_form or forms.ConfirmForm(
                lease=payment.lease, can_allocate=can(m, "payments.allocate", prop))
            ctx["reject_form"] = reject_form or forms.ReasonForm(prefix="reject")
        if can_reverse:
            ctx["reverse_form"] = reverse_form or forms.ReasonForm(prefix="reverse")
        return ctx

    def get(self, request, public_id):
        return render(request, self.template_name, self.context(request, _get_payment(request, public_id)))

    def post(self, request, public_id):
        payment = _get_payment(request, public_id)
        action = request.POST.get("action", "")
        if action not in self.ACTIONS:
            return redirect("payments:detail", public_id=payment.public_id)
        m = request.membership
        if action == "confirm":
            form = forms.ConfirmForm(request.POST, lease=payment.lease,
                                     can_allocate=can(m, "payments.allocate", payment.lease.unit.property))
        else:
            form = forms.ReasonForm(request.POST, prefix=action)
        if form.is_valid():
            try:
                if action == "confirm":
                    services.confirm_payment(m, payment, allocations=form.allocations(), request=request)
                    message = _("Payment confirmed and receipt issued.")
                elif action == "reject":
                    services.reject_payment(m, payment, reason=form.cleaned_data["reason"], request=request)
                    message = _("Payment rejected.")
                else:
                    services.reverse_payment(m, payment, reason=form.cleaned_data["reason"], request=request)
                    message = _("Payment reversed. The invoices and balance are back as they were.")
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, message)
                return redirect("payments:detail", public_id=payment.public_id)
        payment.refresh_from_db()
        return render(request, self.template_name, self.context(request, payment, **{f"{action}_form": form}))


class ReceiptView(CapabilityRequiredMixin, View):
    """Serves the stored PDF only to members who may see the payment."""

    required_capability = "payments.view"

    def get(self, request, public_id):
        payment = _get_payment(request, public_id)
        receipt = getattr(payment, "receipt", None)
        if receipt is None or not receipt.pdf:
            raise Http404
        return FileResponse(receipt.pdf.open("rb"), content_type="application/pdf",
                            as_attachment=request.GET.get("download") == "1", filename=f"{receipt.number}.pdf")


class ApplyCreditView(CapabilityRequiredMixin, View):
    required_capability = "payments.allocate"

    def post(self, request, public_id):
        lease = get_object_or_404(visible_leases(request.membership, Lease.all_objects.all())
                                  .select_related("unit__property"), public_id=public_id)
        try:
            made = services.apply_credit(request.membership, lease, request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            if made:
                messages.success(request, _("Credit applied to %(count)d invoice(s).") % {"count": len(made)})
            else:
                messages.info(request, _("There were no open invoices to put the credit towards."))
        return redirect("billing:lease_account", public_id=lease.public_id)
