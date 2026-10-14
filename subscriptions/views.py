"""The subscription page (D-060 item 12): plan, usage, invoices, SMS credit and choosing a plan; paying
online (D-126)."""

import uuid

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views import View

from accounts import sender_ids
from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can
from ai_assistant import services as assistant
from growth.content import FEATURES
from meters import ocr

from . import collect, documents, entitlements, services
from .models import (
    Plan,
    PlatformCheckout,
    PlatformReceipt,
    SmsWalletEntry,
    Subscription,
    SubscriptionInvoice,
    SubscriptionPayment,
)


class SubscriptionView(CapabilityRequiredMixin, View):
    required_capability = "subscription.view"
    template_name = "subscriptions/subscription.html"

    def get(self, request):
        org = request.organization
        sub = services.subscription_for(org)
        used, allowed = entitlements.usage(org), entitlements.limits(sub.plan)
        plans = list(services.self_serve_plans())
        for plan in plans:
            plan.over = entitlements.over_limits(org, plan)
            plan.current = plan.pk == sub.plan_id and sub.status != Subscription.Status.TRIAL
            plan.offer_list = plan.offers()
        payments = (SubscriptionPayment.objects.filter(organization=org).select_related("receipt", "invoice")
                    .order_by("-paid_on", "-pk")[:20])
        open_invoice = services.open_invoice(sub)
        last_checkout = open_invoice.checkouts.first() if open_invoice else None
        if (last_checkout and last_checkout.provider == PlatformCheckout.Provider.PAYSTACK and last_checkout.phone
                and last_checkout.status == PlatformCheckout.Status.PENDING):
            # A Paystack M-Pesa prompt: ask Paystack, so the receipt is here without waiting for the webhook.
            last_checkout = collect.verify_paystack(last_checkout)
            if last_checkout.status == PlatformCheckout.Status.PAID:
                messages.success(request, _("Thank you: payment received."))
                return redirect("subscriptions:page")
        return render(request, self.template_name, {
            "sub": sub,
            "usage": [(entitlements.LABELS[k], used[k], allowed[k]) for k in ("units", "seats")],
            "open_invoice": open_invoice,
            "account_number": collect.account_number(open_invoice) if open_invoice else "",
            "pay_online": collect.methods() if open_invoice and open_invoice.outstanding > 0 else {},
            "last_checkout": last_checkout,
            "phone": request.user.phone,
            "invoices": SubscriptionInvoice.objects.filter(organization=org)[:24],
            "payments": payments,
            "plans": plans,
            "best_saving": services.best_yearly_saving(plans),
            "features": FEATURES,
            "intervals": Subscription.Interval.choices,
            "can_manage": can(request.membership, "subscription.manage"),
            "paybill": collect.paybill(),
            "sms": services.sms_summary(org),
            "sms_top_up": collect.methods(),
            "sms_top_up_min": services.SMS_TOP_UP_MIN,
            "sms_top_up_max": services.SMS_TOP_UP_MAX,
            "sms_entries": SmsWalletEntry.objects.filter(organization=org)[:10],
            "scans": ocr.summary(org),
            "assistant": assistant.month_summary(org),
            "org": org,
            "support_whatsapp": "".join(c for c in getattr(settings, "SUPPORT_WHATSAPP", "") if c.isdigit()),
            "platform_sender": getattr(settings, "AT_SENDER_ID", "") or "NyumbaPMS",
        })

    def post(self, request):
        if not can(request.membership, "subscription.manage"):
            raise PermissionDenied("subscription.manage")
        plan = Plan.objects.filter(key=request.POST.get("plan", "")).first()
        if plan is None:
            messages.error(request, _("Choose a plan."))
            return redirect("subscriptions:page")
        try:
            invoice = services.choose_plan(request.membership, plan, request.POST.get("interval", ""),
                                           request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            if invoice is None:
                messages.success(request, _("You are now on the %(plan)s plan.") % {"plan": plan.name})
            else:
                messages.success(request, _("Invoice %(number)s issued. Your %(plan)s plan starts when it is paid.")
                                 % {"number": invoice.number, "plan": plan.name})
        return redirect("subscriptions:page")


class SenderNameView(CapabilityRequiredMixin, View):
    """Asks for the organization's own SMS sender name, or goes back to the platform's (D-120)."""

    required_capability = "subscription.manage"

    def post(self, request):
        try:
            if request.POST.get("action") == "cancel":
                sender_ids.cancel(request.membership, request=request)
                messages.success(request, _("Your SMS will go out under the platform name."))
            else:
                sender_ids.request_name(request.membership, request.POST.get("name", ""), request=request)
                messages.success(request, _("Asked. We will register the name with the networks and tell you the "
                                            "fee; until it is approved, SMS keep going under the platform name."))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect("subscriptions:page")


def _pdf(content: bytes, name: str) -> HttpResponse:
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{name}.pdf"'
    return response


class InvoicePdfView(CapabilityRequiredMixin, View):
    required_capability = "subscription.view"

    def get(self, request, public_id):
        invoice = SubscriptionInvoice.objects.filter(organization=request.organization, public_id=public_id).first()
        if invoice is None:
            raise Http404
        return _pdf(documents.invoice_pdf(invoice), invoice.number)


class ReceiptPdfView(CapabilityRequiredMixin, View):
    required_capability = "subscription.view"

    def get(self, request, public_id):
        receipt = (PlatformReceipt.objects.select_related("payment__organization", "payment__invoice")
                   .filter(payment__organization=request.organization, public_id=public_id).first())
        if receipt is None:
            raise Http404
        return _pdf(documents.receipt_pdf(receipt), receipt.number)


def _invoice(request) -> SubscriptionInvoice:
    try:
        public_id = uuid.UUID(request.POST.get("invoice", ""))
    except ValueError:
        raise Http404 from None
    return get_object_or_404(SubscriptionInvoice, organization=request.organization, public_id=public_id)


class PayMpesaView(CapabilityRequiredMixin, View):
    """Sends an M-Pesa prompt for the open invoice to the phone given (D-126)."""

    required_capability = "subscription.manage"

    def post(self, request):
        invoice = _invoice(request)
        try:
            collect.start_mpesa(request.membership, invoice, request.POST.get("phone", ""), request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Check your phone and enter your M-Pesa PIN. The receipt shows here once "
                                        "Safaricom confirms it; refresh the page in a minute."))
        return redirect("subscriptions:page")


class PayPaystackView(CapabilityRequiredMixin, View):
    """Opens Paystack's checkout page for the open invoice (D-126)."""

    required_capability = "subscription.manage"

    def post(self, request):
        invoice = _invoice(request)
        try:
            page = collect.start_paystack(request.membership, invoice, request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("subscriptions:page")
        return redirect(page)


class PayPaystackMpesaView(CapabilityRequiredMixin, View):
    """Sends an M-Pesa prompt through Paystack, for when our own prompt is down."""

    required_capability = "subscription.manage"

    def post(self, request):
        invoice = _invoice(request)
        try:
            collect.start_paystack_mpesa(request.membership, invoice, request.POST.get("phone", ""), request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Check your phone and enter your M-Pesa PIN. The receipt shows here once "
                                        "it is confirmed; refresh the page in a minute."))
        return redirect("subscriptions:page")


class SmsTopUpView(CapabilityRequiredMixin, View):
    """Tops up the SMS wallet by M-Pesa prompt or Paystack (D-142)."""

    required_capability = "subscription.manage"

    def post(self, request):
        amount = request.POST.get("amount", "")
        try:
            if request.POST.get("method") == "paystack":
                return redirect(collect.start_sms_paystack(request.membership, amount, request=request))
            collect.start_sms_mpesa(request.membership, amount, request.POST.get("phone", ""), request=request)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, _("Check your phone and enter your M-Pesa PIN. The credit shows here once "
                                        "Safaricom confirms it; refresh the page in a minute."))
        return redirect("subscriptions:page")


class PayStatusView(CapabilityRequiredMixin, View):
    """What the waiting notice on the subscription page asks every few seconds, so the page can say
    "payment successful" by itself once the prompt is approved."""

    required_capability = "subscription.view"

    def get(self, request, public_id):
        checkout = get_object_or_404(PlatformCheckout, public_id=public_id, organization=request.organization)
        if (checkout.status == PlatformCheckout.Status.PENDING
                and checkout.provider == PlatformCheckout.Provider.PAYSTACK):
            checkout = collect.verify_paystack(checkout)
        return JsonResponse({"status": checkout.status, "result": checkout.result})


class PaystackReturnView(CapabilityRequiredMixin, View):
    """Where Paystack sends the landlord back: asks Paystack how it went, so the receipt is there at once."""

    required_capability = "subscription.view"

    def get(self, request):
        checkout = PlatformCheckout.objects.filter(
            organization=request.organization, provider=PlatformCheckout.Provider.PAYSTACK,
            reference=request.GET.get("reference", "")[:80]).first()
        if checkout is None:
            return redirect("subscriptions:page")
        checkout = collect.verify_paystack(checkout)
        if checkout.status == PlatformCheckout.Status.PAID:
            messages.success(request, _("Thank you: payment received."))
        elif checkout.status == PlatformCheckout.Status.FAILED:
            messages.error(request, _("The payment did not go through: %(why)s") % {"why": checkout.result})
        else:
            messages.info(request, _("Paystack has not confirmed the payment yet. Refresh this page in a minute."))
        return redirect("subscriptions:page")
