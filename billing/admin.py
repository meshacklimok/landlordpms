from django.contrib import admin

from .models import ChargeType, DepositEntry, FollowUp, Invoice, InvoiceLine, LedgerEntry


@admin.register(ChargeType)
class ChargeTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "category", "is_system", "organization", "archived_at"]
    list_filter = ["category", "is_system"]
    search_fields = ["name", "organization__name"]
    readonly_fields = ["public_id", "is_system", "created_at", "updated_at", "archived_at", "archived_by"]

    def get_queryset(self, request):
        return ChargeType.all_objects.select_related("organization")

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyAdmin(admin.ModelAdmin):
    """Money records are changed only through services, so every change is audited (doc 11 §21)."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    fields = ["charge_type", "description", "quantity", "unit_price", "amount", "billing_month", "is_void"]
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Invoice)
class InvoiceAdmin(ReadOnlyAdmin):
    list_display = ["number", "status", "lease", "period_start", "due_date", "total", "amount_paid", "organization"]
    list_filter = ["status"]
    search_fields = ["number", "lease__number", "organization__name"]
    date_hierarchy = "period_start"
    inlines = [InvoiceLineInline]
    list_select_related = ["lease", "organization"]


@admin.register(LedgerEntry)
class LedgerEntryAdmin(ReadOnlyAdmin):
    list_display = ["entry_date", "kind", "amount", "lease", "invoice", "organization"]
    list_filter = ["kind"]
    search_fields = ["lease__number", "invoice__number", "organization__name"]
    date_hierarchy = "entry_date"
    list_select_related = ["lease", "invoice", "organization"]


@admin.register(DepositEntry)
class DepositEntryAdmin(ReadOnlyAdmin):
    list_display = ["entry_date", "kind", "deposit_type", "amount", "lease", "organization"]
    list_filter = ["kind", "deposit_type"]
    search_fields = ["lease__number", "reference", "organization__name"]
    date_hierarchy = "entry_date"
    list_select_related = ["lease", "organization"]


@admin.register(FollowUp)
class FollowUpAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "outcome", "promised_on", "promised_amount", "lease", "organization"]
    list_filter = ["outcome"]
    search_fields = ["lease__number", "organization__name"]
    list_select_related = ["lease", "organization"]
