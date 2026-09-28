from django.contrib import admin

from .models import DarajaCredentials


@admin.register(DarajaCredentials)
class DarajaCredentialsAdmin(admin.ModelAdmin):
    """Read-only: secrets are edited on the M-Pesa settings page and never shown."""

    list_display = ["payment_account", "shortcode", "environment", "urls_registered_at", "organization"]
    list_filter = ["environment"]
    fields = ["organization", "payment_account", "environment", "shortcode", "urls_registered_at",
              "registration_error", "updated_by", "created_at", "updated_at"]
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
