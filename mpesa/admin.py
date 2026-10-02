from django.contrib import admin

from .models import DarajaCredentials, MpesaTransaction, StkRequest


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


@admin.register(MpesaTransaction)
class MpesaTransactionAdmin(admin.ModelAdmin):
    """Read-only: transactions are matched or ignored in the app and never edited or deleted."""

    list_display = ["trans_id", "amount", "paid_at", "bill_ref", "status", "payment_account", "organization"]
    list_filter = ["status", "source"]
    search_fields = ["trans_id", "bill_ref"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StkRequest)
class StkRequestAdmin(admin.ModelAdmin):
    """Read-only: requests are sent from the lease and settled by Safaricom's callback."""

    list_display = ["created_at", "lease", "phone", "amount", "status", "result_code", "organization"]
    list_filter = ["status"]
    search_fields = ["checkout_request_id", "phone"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
