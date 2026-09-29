"""The subscription page (D-060 item 12): plan, usage, invoices, SMS credit and choosing a plan."""

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can

from . import documents, entitlements, services
from .models import Plan, PlatformReceipt, SmsWalletEntry, Subscription, SubscriptionInvoice, SubscriptionPayment


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
        payments = (SubscriptionPayment.objects.filter(organization=org).select_related("receipt", "invoice")
                    .order_by("-paid_on", "-pk")[:20])
        return render(request, self.template_name, {
            "sub": sub,
            "usage": [(entitlements.LABELS[k], used[k], allowed[k]) for k in ("units", "seats")],
            "open_invoice": services.open_invoice(sub),
            "invoices": SubscriptionInvoice.objects.filter(organization=org)[:24],
            "payments": payments,
            "plans": plans,
            "intervals": Subscription.Interval.choices,
            "can_manage": can(request.membership, "subscription.manage"),
            "paybill": getattr(settings, "PLATFORM_PAYBILL", ""),
            "sms_balance": services.sms_balance(org),
            "sms_price": services.sms_price(),
            "sms_enforced": getattr(settings, "SMS_WALLET_ENFORCED", False),
            "sms_entries": SmsWalletEntry.objects.filter(organization=org)[:10],
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
