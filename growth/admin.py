"""Demo requests, referrals and sign-up sources, for Platform Admins."""

from django.contrib import admin

from audit import services as audit

from .models import DemoRequest, Referral, ReferralCode, ReferralCredit, ReferralPolicy, SignupSource


@admin.register(DemoRequest)
class DemoRequestAdmin(admin.ModelAdmin):
    list_display = ["created_at", "full_name", "phone", "kind", "size", "preferred_date", "preferred_slot",
                    "channel", "source", "status"]
    list_filter = ["status", "kind", "size", "channel"]
    search_fields = ["full_name", "phone", "email", "organization_name"]
    date_hierarchy = "created_at"
    list_editable = ["status"]
    fields = ["created_at", "full_name", "phone", "email", "organization_name", "kind", "size", "channel",
              "preferred_date", "preferred_slot", "message", "source", "campaign", "status", "internal_note"]
    readonly_fields = ["created_at", "source", "campaign"]


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ["created_at", "referrer", "referred", "code", "status", "reward_percent", "reward_days",
                    "rewarded_at"]
    list_filter = ["status"]
    search_fields = ["referrer__name", "referred__name", "code"]
    readonly_fields = [f.name for f in Referral._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(ReferralPolicy)
class ReferralPolicyAdmin(admin.ModelAdmin):
    """What a referral earns and when it counts (D-133). One row; the daily job applies it to referrals waiting."""

    fieldsets = [
        ("Referrer's reward", {"fields": ["reward_kind", "discount_percent", "reward_days",
                                          "max_rewards_per_year"]}),
        ("Invited landlord's reward", {"fields": ["reward_referred_too", "referred_reward_kind",
                                                   "referred_discount_percent", "referred_reward_days"]}),
        ("When it counts", {"fields": ["require_referrer_paid", "require_payment", "min_properties", "min_units", "min_tenants",
                                       "min_leases", "min_invoices"]}),
    ]

    def changelist_view(self, request, extra_context=None):
        ReferralPolicy.get()
        return super().changelist_view(request, extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit.record("platform.referral_policy", actor=request.user, obj=obj,
                     changes={f: str(form.cleaned_data[f]) for f in form.changed_data})

    def has_add_permission(self, request):
        return not ReferralPolicy.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ReferralCredit)
class ReferralCreditAdmin(admin.ModelAdmin):
    list_display = ["created_at", "organization", "percent", "invoice", "used_at"]
    list_filter = [("used_at", admin.EmptyFieldListFilter)]
    search_fields = ["organization__name"]
    readonly_fields = [f.name for f in ReferralCredit._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ["organization", "code", "created_at"]
    search_fields = ["organization__name", "code"]
    readonly_fields = ["organization", "code"]

    def has_add_permission(self, request):
        return False


@admin.register(SignupSource)
class SignupSourceAdmin(admin.ModelAdmin):
    list_display = ["created_at", "organization", "source", "medium", "campaign", "referrer_host", "landing_path"]
    list_filter = ["source", "medium"]
    search_fields = ["organization__name", "campaign", "referrer_host"]
    readonly_fields = [f.name for f in SignupSource._meta.fields]

    def has_add_permission(self, request):
        return False
