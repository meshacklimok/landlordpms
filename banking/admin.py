from django.contrib import admin

from .models import BankTransaction, StatementImport


@admin.register(StatementImport)
class StatementImportAdmin(admin.ModelAdmin):
    list_display = ["created_at", "organization", "payment_account", "kind", "status", "new_count", "matched_count"]
    list_filter = ["kind", "status"]
    readonly_fields = [f.name for f in StatementImport._meta.fields if f.name != "rows"]
    exclude = ["rows"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = ["posted_on", "organization", "payment_account", "amount", "reference", "status"]
    list_filter = ["status"]
    search_fields = ["reference", "description"]
    readonly_fields = [f.name for f in BankTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
