"""Platform billing in admin (D-060 item 7): Platform Admins record payments and SMS top-ups here."""

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from accounts.models import Organization
from audit import services as audit

from . import documents, services
from .models import (
    Plan,
    PlatformReceipt,
    SmsWallet,
    SmsWalletEntry,
    Subscription,
    SubscriptionInvoice,
    SubscriptionPayment,
)


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ["name", "key", "unit_limit", "seat_limit", "monthly_price", "yearly_price", "self_serve",
                    "is_active", "sort_order"]
    list_editable = ["sort_order"]

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["organization", "plan", "status", "interval", "trial_ends_on", "period_end", "past_due_since"]
    list_filter = ["status", "plan"]
    search_fields = ["organization__name"]
    raw_id_fields = ["organization"]
    fields = ["organization", "plan", "status", "interval", "trial_ends_on", "period_start", "period_end",
              "past_due_since"]
    watched = fields[1:]

    def get_readonly_fields(self, request, obj=None):
        return ["organization"] if obj else []

    def save_model(self, request, obj, form, change):
        before = Subscription.objects.filter(pk=obj.pk).first() if change else None
        super().save_model(request, obj, form, change)
        changes = {f: [str(getattr(before, f)) if before else None, str(getattr(obj, f))] for f in self.watched
                   if before is None or getattr(before, f) != getattr(obj, f)}
        audit.record("subscription.admin_change", actor=request.user, organization=obj.organization, obj=obj,
                     request=request, changes=changes)

    def has_delete_permission(self, request, obj=None):
        return False


class PaymentInline(admin.TabularInline):
    model = SubscriptionPayment
    extra = 0
    fields = ["reference", "amount", "method", "paid_on", "recorded_by"]
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SubscriptionInvoice)
class SubscriptionInvoiceAdmin(admin.ModelAdmin):
    list_display = ["number", "organization", "plan_name", "interval", "reason", "total", "status", "issued_on",
                    "due_on", "pdf_link"]
    list_filter = ["status", "reason", "interval"]
    search_fields = ["number", "organization__name", "customer_name"]
    date_hierarchy = "issued_on"
    inlines = [PaymentInline]
    actions = ["void"]

    def get_urls(self):
        return [path("<int:pk>/pdf/", self.admin_site.admin_view(self.pdf), name="subscriptions_invoice_pdf"),
                *super().get_urls()]

    def pdf(self, request, pk):
        invoice = SubscriptionInvoice.objects.get(pk=pk)
        response = HttpResponse(documents.invoice_pdf(invoice), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{invoice.number}.pdf"'
        return response

    @admin.display(description="PDF")
    def pdf_link(self, obj):
        return format_html('<a href="{}">PDF</a>', reverse("admin:subscriptions_invoice_pdf", args=[obj.pk]))

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    @admin.action(description="Void selected open invoices with no payments")
    def void(self, request, queryset):
        done = 0
        for invoice in queryset:
            try:
                services.void_invoice(request.user, invoice, "Voided by a Platform Admin", request=request)
                done += 1
            except ValidationError as exc:
                self.message_user(request, f"{invoice.number}: {' '.join(exc.messages)}", messages.ERROR)
        if done:
            self.message_user(request, f"{done} invoice(s) voided.")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PaymentForm(forms.ModelForm):
    """One payment: for an open invoice, or an SMS top-up for an organization."""

    organization = forms.ModelChoiceField(Organization.objects.all(), required=False,
                                          help_text="For an SMS top-up. Leave empty when paying an invoice.")

    class Meta:
        model = SubscriptionPayment
        fields = ["invoice", "organization", "amount", "method", "reference", "paid_on", "note"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["invoice"].queryset = SubscriptionInvoice.objects.filter(
            status=SubscriptionInvoice.Status.OPEN).select_related("organization")
        self.fields["paid_on"].initial = timezone.localdate()

    def clean(self):
        data = super().clean()
        if bool(data.get("invoice")) == bool(data.get("organization")):
            raise ValidationError("Choose an open invoice, or an organization for an SMS top-up, not both.")
        if data.get("paid_on") and "reference" in data:
            data["reference"] = services.check_payment(data.get("amount"), data["reference"], data["paid_on"])
        return data

    def _post_clean(self):
        # The service creates the row; the model's own checks would only repeat them.
        pass


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    form = PaymentForm
    list_display = ["reference", "organization", "amount", "method", "paid_on", "invoice", "receipt_link",
                    "recorded_by"]
    list_filter = ["method"]
    search_fields = ["reference", "organization__name", "invoice__number"]
    date_hierarchy = "paid_on"

    def get_urls(self):
        return [path("<int:pk>/receipt/", self.admin_site.admin_view(self.receipt), name="subscriptions_receipt_pdf"),
                *super().get_urls()]

    def receipt(self, request, pk):
        receipt = PlatformReceipt.objects.select_related("payment__organization", "payment__invoice").get(
            payment_id=pk)
        response = HttpResponse(documents.receipt_pdf(receipt), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{receipt.number}.pdf"'
        return response

    @admin.display(description="Receipt")
    def receipt_link(self, obj):
        receipt = getattr(obj, "receipt", None)
        if receipt is None:
            return "-"
        return format_html('<a href="{}">{}</a>', reverse("admin:subscriptions_receipt_pdf", args=[obj.pk]),
                           receipt.number)

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields] if obj else []

    def save_model(self, request, obj, form, change):
        data = form.cleaned_data
        common = {"amount": data["amount"], "method": data["method"], "reference": data["reference"],
                  "paid_on": data["paid_on"], "note": data.get("note", ""), "request": request}
        if data.get("invoice"):
            payment = services.record_payment(request.user, data["invoice"], **common)
        else:
            payment = services.top_up_sms(request.user, data["organization"], **common).payment
        obj.pk = payment.pk
        obj.__dict__.update({k: v for k, v in payment.__dict__.items() if not k.startswith("_")})

    def has_change_permission(self, request, obj=None):
        return obj is None

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SmsWallet)
class SmsWalletAdmin(admin.ModelAdmin):
    list_display = ["organization", "balance"]
    search_fields = ["organization__name"]
    readonly_fields = ["organization", "balance"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SmsWalletEntry)
class SmsWalletEntryAdmin(admin.ModelAdmin):
    list_display = ["created_at", "organization", "kind", "amount", "balance_after", "note"]
    list_filter = ["kind"]
    search_fields = ["organization__name", "note"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
