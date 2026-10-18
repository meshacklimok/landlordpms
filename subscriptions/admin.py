"""Platform billing in admin (D-060 item 7): Platform Admins record payments and SMS top-ups here."""


from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import DecimalField, IntegerField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from accounts.models import Organization
from audit import services as audit
from core import exports

from . import documents, services
from .models import (
    BillingPolicy,
    Plan,
    PlatformCheckout,
    PlatformPaySwitch,
    PlatformReceipt,
    PlatformTransaction,
    SmsWallet,
    SmsWalletEntry,
    Subscription,
    SubscriptionInvoice,
    SubscriptionPayment,
)


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    """Bands and prices (D-132). Prices are before VAT. A one-off price of 0 means no one-off offer; Free is
    a plan with every price 0 and is offered only while "is active" is ticked."""

    list_display = ["name", "key", "unit_limit", "seat_limit", "monthly_price", "yearly_price", "one_off_price",
                    "one_off_years", "badge", "self_serve", "is_active", "sort_order"]
    list_editable = ["unit_limit", "seat_limit", "monthly_price", "yearly_price", "one_off_price",
                     "one_off_years", "badge", "self_serve", "is_active", "sort_order"]
    watched = ["unit_limit", "seat_limit", "monthly_price", "yearly_price", "one_off_price", "one_off_years", "badge",
               "features", "self_serve", "is_active"]

    fieldsets = [
        (None, {"fields": ["key", "name", "badge", "sort_order", "self_serve", "is_active"]}),
        (_("Limits"), {"fields": ["unit_limit", "seat_limit"]}),
        (_("Prices (before VAT)"), {"fields": ["monthly_price", "yearly_price", "one_off_price", "one_off_years"]}),
        (_("Features on the pricing card"), {"fields": ["features"]}),
    ]

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == "features":
            field.widget.attrs.update(rows=8, cols=70)
        return field

    def save_model(self, request, obj, form, change):
        before = Plan.objects.filter(pk=obj.pk).first() if change else None
        super().save_model(request, obj, form, change)
        changes = {f: [str(getattr(before, f)) if before else None, str(getattr(obj, f))] for f in self.watched
                   if before is None or getattr(before, f) != getattr(obj, f)}
        if changes:
            audit.record("plan.admin_change", actor=request.user, obj=obj, request=request, changes=changes)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["organization", "plan", "status", "interval", "trial_ends_on", "period_end", "past_due_since"]
    list_filter = ["status", "plan"]
    search_fields = ["organization__name"]
    raw_id_fields = ["organization"]
    fields = ["organization", "plan", "status", "interval", "trial_ends_on", "period_start", "period_end",
              "past_due_since", "grace_until", "next_plan", "next_interval"]
    watched = fields[1:]
    actions = ["extend_grace_7", "extend_grace_30"]

    def get_readonly_fields(self, request, obj=None):
        return ["organization"] if obj else []

    def _extend(self, request, queryset, days):
        for sub in queryset:
            try:
                until = services.extend_grace(request.user, sub, days, "Extended by a Platform Admin (dispute)",
                                              request=request)
                self.message_user(request, f"{sub.organization}: no lapse until {until}.")
            except ValidationError as exc:
                self.message_user(request, f"{sub.organization}: {' '.join(exc.messages)}", messages.ERROR)

    @admin.action(description="Hold off the lapse for 7 more days")
    def extend_grace_7(self, request, queryset):
        self._extend(request, queryset, 7)

    @admin.action(description="Hold off the lapse for 30 more days")
    def extend_grace_30(self, request, queryset):
        self._extend(request, queryset, 30)

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
    list_filter = ["status", "reason", "interval", ("disputed_at", admin.EmptyFieldListFilter)]
    search_fields = ["number", "organization__name", "customer_name"]
    date_hierarchy = "issued_on"
    inlines = [PaymentInline]
    actions = ["void", "mark_disputed", "clear_dispute"]

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
        return [f.name for f in self.model._meta.fields if f.name != "dispute_note"]

    def save_model(self, request, obj, form, change):
        # Only the dispute note can be edited here.
        SubscriptionInvoice.objects.filter(pk=obj.pk).update(dispute_note=obj.dispute_note)
        audit.record("subscription_invoice.dispute_note", actor=request.user, organization=obj.organization, obj=obj,
                     request=request, changes={"note": obj.dispute_note})

    def _dispute(self, request, queryset, resolved):
        done = 0
        for invoice in queryset:
            try:
                services.set_dispute(request.user, invoice, resolved=resolved, request=request)
                done += 1
            except ValidationError as exc:
                self.message_user(request, f"{invoice.number}: {' '.join(exc.messages)}", messages.ERROR)
        if done:
            self.message_user(request, f"{done} invoice(s) updated. Write the reason in each invoice's dispute note.")

    @admin.action(description="Mark as disputed (no lapse while open)")
    def mark_disputed(self, request, queryset):
        self._dispute(request, queryset, False)

    @admin.action(description="Clear the dispute")
    def clear_dispute(self, request, queryset):
        self._dispute(request, queryset, True)

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
    """SMS for every organization this month (D-142): who pays, parts used, what Africa's Talking charged us,
    what we billed them, and the wallet. Mode and monthly parts are edited right in the list."""

    list_display = ["organization", "mode", "monthly_cap", "used", "state", "our_cost", "billed", "balance"]
    list_editable = ["mode", "monthly_cap"]
    list_filter = ["mode"]
    search_fields = ["organization__name"]
    readonly_fields = ["organization", "balance"]
    fields = ["organization", "mode", "monthly_cap", "balance"]
    actions = ["export_csv"]
    ordering = ["organization__name"]

    def get_queryset(self, request):
        entries = SmsWalletEntry.objects.filter(
            organization=OuterRef("organization"), kind=SmsWalletEntry.Kind.MESSAGE,
            created_at__gte=services.month_start()).order_by().values("organization")

        def total(expr, field):
            return Coalesce(Subquery(entries.annotate(t=Sum(expr)).values("t")), Value(0), output_field=field)

        money = DecimalField(max_digits=14, decimal_places=4)
        return super().get_queryset(request).select_related("organization").annotate(
            parts_used=total("segments", IntegerField()), cost=total("message__cost", money),
            charged=total("amount", money))

    @admin.display(description="Parts this month", ordering="parts_used")
    def used(self, obj):
        return obj.parts_used

    @admin.display(description="State")
    def state(self, obj):
        cap = obj.monthly_cap
        if obj.mode == SmsWallet.Mode.PREPAID:
            return "No credit" if obj.balance <= 0 else "OK"
        if cap is None:
            return "No cap"
        if obj.parts_used >= cap:
            return "Cap reached" if obj.mode == SmsWallet.Mode.SPONSORED else "Allowance used"
        return f"{round(obj.parts_used * 100 / cap)}% of {cap}" if cap else "OK"

    @admin.display(description="Our cost (KES)", ordering="cost")
    def our_cost(self, obj):
        return f"{obj.cost:.2f}"

    @admin.display(description="Billed to them (KES)", ordering="charged")
    def billed(self, obj):
        return f"{-obj.charged:.2f}"

    def changelist_view(self, request, extra_context=None):
        missing = Organization.objects.filter(sms_wallet__isnull=True)
        SmsWallet.objects.bulk_create([SmsWallet(organization=o) for o in missing])
        rows = SmsWalletEntry.objects.filter(kind=SmsWalletEntry.Kind.MESSAGE, created_at__gte=services.month_start())
        everyone = rows.aggregate(parts=Sum("segments"), cost=Sum("message__cost"), billed=Sum("amount"))
        sponsored = rows.filter(organization__sms_wallet__mode=SmsWallet.Mode.SPONSORED).aggregate(
            cost=Sum("message__cost"))
        title = (f"SMS this month: {everyone['parts'] or 0} parts, our cost KES {everyone['cost'] or 0:.2f}, "
                 f"billed KES {-(everyone['billed'] or 0):.2f}; sponsored by us KES {sponsored['cost'] or 0:.2f}")
        return super().changelist_view(request, {**(extra_context or {}), "title": title})

    def save_model(self, request, obj, form, change):
        before = SmsWallet.objects.filter(pk=obj.pk).values("mode", "monthly_cap").first()
        super().save_model(request, obj, form, change)
        now = {"mode": obj.mode, "monthly_cap": obj.monthly_cap}
        if before and before != now:
            SmsWallet.objects.filter(pk=obj.pk).update(alert_level=0)  # new terms, so warn afresh
            audit.record("sms_wallet.terms", actor=request.user, organization=obj.organization, obj=obj,
                         request=request,
                         changes={k: [str(before[k]), str(now[k])] for k in now if before[k] != now[k]})

    @admin.action(description="Export selected to CSV")
    def export_csv(self, request, queryset):
        response = exports.csv_response("sms-usage.csv")
        writer = exports.writer(response)
        writer.writerow(["Organization", "Mode", "Monthly parts", "Parts this month", "Our cost KES",
                         "Billed KES", "Balance KES"])
        for w in queryset:
            writer.writerow([w.organization.name, w.mode, w.monthly_cap if w.monthly_cap is not None else "",
                             w.parts_used, f"{w.cost:.2f}", f"{-w.charged:.2f}", w.balance])
        return response

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SmsWalletEntry)
class SmsWalletEntryAdmin(admin.ModelAdmin):
    list_display = ["created_at", "organization", "kind", "segments", "amount", "balance_after", "note"]
    list_filter = ["kind"]
    search_fields = ["organization__name", "note"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BillingPolicy)
class BillingPolicyAdmin(admin.ModelAdmin):
    """Trial length, renewal invoice lead time, grace days and reminder days (D-165). One row. A lapsed account
    is read-only, never locked out of its records."""

    list_display = ["__str__", "trial_days", "invoice_lead_days", "grace_days", "reminder_days",
                    "grace_reminder_days", "updated_at"]
    fields = ["trial_days", "invoice_lead_days", "grace_days", "reminder_days", "grace_reminder_days"]

    def changelist_view(self, request, extra_context=None):
        BillingPolicy.get()
        return super().changelist_view(request, extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit.record("platform.billing_policy", actor=request.user, obj=obj,
                     changes={f: str(getattr(obj, f)) for f in self.fields})

    def has_add_permission(self, request):
        return not BillingPolicy.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PlatformPaySwitch)
class PlatformPaySwitchAdmin(admin.ModelAdmin):
    """Turns each online way of paying the subscription on or off (D-126). Keys stay in the environment."""

    list_display = ["__str__", "mpesa_on", "paystack_on", "configured", "updated_at"]
    fields = ["mpesa_on", "paystack_on", "configured"]
    readonly_fields = ["configured"]

    @admin.display(description="Keys set on the server")
    def configured(self, obj):
        from . import collect

        return (f"M-Pesa prompt: {'yes' if collect.prompt_configured() else 'no'}; "
                f"Paybill confirmations: {'yes' if collect.mpesa_configured() else 'no'}; "
                f"Paystack: {'yes' if collect.paystack_configured() else 'no'}")

    def changelist_view(self, request, extra_context=None):
        PlatformPaySwitch.get()
        return super().changelist_view(request, extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit.record("platform.pay_switch", actor=request.user, obj=obj,
                     changes={"mpesa_on": obj.mpesa_on, "paystack_on": obj.paystack_on})

    def has_add_permission(self, request):
        return not PlatformPaySwitch.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PlatformCheckout)
class PlatformCheckoutAdmin(admin.ModelAdmin):
    list_display = ["created_at", "organization", "invoice", "provider", "amount", "phone", "status", "result"]
    list_filter = ["provider", "status"]
    search_fields = ["organization__name", "invoice__number", "reference", "phone"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PlatformTransaction)
class PlatformTransactionAdmin(admin.ModelAdmin):
    """Money into our Paybill or Paystack. "Needs a person": record it under Subscription payments with the
    same code as its reference, for the right invoice or as an SMS top-up; that clears it."""

    list_display = ["created_at", "code", "source", "amount", "account", "payer_name", "status", "invoice", "note"]
    list_filter = ["status", "source"]
    search_fields = ["code", "account", "payer_name", "phone", "invoice__number"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
