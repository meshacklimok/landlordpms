from django.contrib import admin

from .models import Expense, ExpenseCategory, Supplier


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "archived_at"]
    search_fields = ["name"]
    raw_id_fields = ["organization", "archived_by"]


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "phone", "kra_pin", "archived_at"]
    search_fields = ["name", "phone", "kra_pin"]
    raw_id_fields = ["organization", "created_by", "archived_by"]


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    """Read only: expenses change through the app so every step is checked and audited."""

    list_display = ["number", "property", "category", "amount", "paid_on", "status"]
    list_filter = ["status", "method"]
    search_fields = ["number", "reference", "description", "property__name"]
    raw_id_fields = ["organization", "property", "category", "supplier", "recorded_by"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
