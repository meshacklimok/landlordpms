from django.contrib import admin

from .models import Payment, PaymentAccount, PropertyPaymentAccount


class PropertyPaymentAccountInline(admin.TabularInline):
    model = PropertyPaymentAccount
    extra = 0
    autocomplete_fields = ["property"]
    fields = ["property", "is_default"]


@admin.register(PaymentAccount)
class PaymentAccountAdmin(admin.ModelAdmin):
    list_display = ["display_name", "type", "number", "organization", "archived_at"]
    list_filter = ["type"]
    search_fields = ["display_name", "number", "organization__name"]
    inlines = [PropertyPaymentAccountInline]

    def get_queryset(self, request):
        return PaymentAccount.all_objects.all()

    def save_formset(self, request, form, formset, change):
        for obj in formset.save(commit=False):
            obj.organization_id = form.instance.organization_id
            obj.save()
        for obj in formset.deleted_objects:
            obj.delete()


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Read-only: payments change only through the services, which audit and post to the ledger."""

    list_display = ["public_id", "lease", "amount", "method", "status", "paid_at", "organization"]
    list_filter = ["status", "method"]
    search_fields = ["reference", "lease__number"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
