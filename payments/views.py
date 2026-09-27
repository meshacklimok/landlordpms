"""Payments: list, record, review, confirm/reject/reverse and receipts. Thin views; services do the work.

Anything outside the member's properties is a 404, never a 403.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can
from billing.invoicing import lease_balance
from leases.models import Lease
from leases.services import visible_leases
from properties.views import _apply_errors

from . import forms, selectors, services
from .models import Payment

PAGE_SIZE = 25


def _get_payment(request, public_id) -> Payment:
    return get_object_or_404(selectors.visible_payments(request.membership), public_id=public_id)


def _get_live_lease(request, public_id) -> Lease:
    qs = visible_leases(request.membership, Lease.objects.exclude(status=Lease.Status.DRAFT))
    return get_object_or_404(qs.select_related("unit__property", "organization"), public_id=public_id)


class PaymentListView(CapabilityRequiredMixin, View):
    template_name = "payments/payment_list.html"
    required_capability = "payments.view"

    def get(self, request):
        q = request.GET.get("q", "").strip()[:60]
        status = request.GET.get("status", "")
        qs = selectors.filter_payments(selectors.visible_payments(request.membership), status=status, q=q)
        page = Paginator(qs.order_by("-paid_at", "-pk"), PAGE_SIZE).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "page": page, "q": q, "status": status,
            "status_filters": [(k, s.label) for k, s in selectors.STATUS_FILTERS.items()],
            "pending_count": selectors.pending_review(request.membership).count(),
        })


class ReviewQueueView(CapabilityRequiredMixin, View):
    template_name = "payments/review.html"
    required_capability = "payments.confirm"

    def get(self, request):
        return render(request, self.template_name, {"payments": selectors.pending_review(request.membership)})


class RecordPaymentView(CapabilityRequiredMixin, View):
    template_name = "payments/record.html"
    required_capability = "payments.record"

    def context(self, request, lease, form=None):
        m = request.membership
        can_allocate = can(m, "payments.allocate", lease.unit.property)
        return {
            "lease": lease, "balance": lease_balance(lease),
            "form": form or forms.PaymentForm(lease=lease, can_allocate=can_allocate),
            "will_confirm": can(m, "payments.confirm", lease.unit.property),
        }

    def get(self, request, public_id):
        lease = _get_live_lease(request, public_id)
        return render(request, self.template_name, self.context(request, lease))

    def post(self, request, public_id):
        lease = _get_live_lease(request, public_id)
        form = forms.PaymentForm(request.POST, lease=lease,
                                 can_allocate=can(request.membership, "payments.allocate", lease.unit.property))
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
        return render(request, self.template_name, self.context(request, lease, form=form))


class PaymentDetailView(CapabilityRequiredMixin, View):
    template_name = "payments/payment_detail.html"
    required_capability = "payments.view"
    ACTIONS = ("confirm", "reject", "reverse")

    def context(self, request, payment, *, confirm_form=None, reject_form=None, reverse_form=None):
        m, prop = request.membership, payment.lease.unit.property
        pending = payment.status == Payment.Status.PENDING_REVIEW
        can_confirm = pending and can(m, "payments.confirm", prop)
        can_reverse = payment.status == Payment.Status.CONFIRMED and can(m, "payments.reverse", prop)
        ctx = {
            "payment": payment, "lease": payment.lease,
            "allocations": payment.allocations.select_related("invoice").order_by("pk"),
            "receipt": getattr(payment, "receipt", None),
            "can_confirm": can_confirm, "can_reverse": can_reverse,
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
