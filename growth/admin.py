"""Demo requests, referrals and sign-up sources, for Platform Admins."""

from django.contrib import admin

from .models import DemoRequest, Referral, ReferralCode, SignupSource


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
    list_display = ["created_at", "referrer", "referred", "code", "status", "reward_days", "rewarded_at"]
    list_filter = ["status"]
    search_fields = ["referrer__name", "referred__name", "code"]
    readonly_fields = [f.name for f in Referral._meta.fields]

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
