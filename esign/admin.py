from django.contrib import admin

from .models import Signer, SigningRequest


class SignerInline(admin.TabularInline):
    model = Signer
    fields = ("role", "name", "phone", "link_sent_at", "verified_at", "signed_at", "signed_name", "signed_ip",
              "declined_at", "decline_reason")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SigningRequest)
class SigningRequestAdmin(admin.ModelAdmin):
    """Read only: sending, signing and cancelling go through esign.services and are audited (D-118)."""

    list_display = ("number", "kind", "status", "organization", "lease", "created_at", "completed_at")
    list_filter = ("kind", "status")
    list_select_related = ("organization", "lease")
    exclude = ("verify_code",)
    inlines = [SignerInline]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
