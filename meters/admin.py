from django.contrib import admin

from .models import Meter, MeterCharge, MeterReading, MeterUnit, Scan


class MeterUnitInline(admin.TabularInline):
    model = MeterUnit
    extra = 0
    raw_id_fields = ["unit"]


@admin.register(Meter)
class MeterAdmin(admin.ModelAdmin):
    list_display = ["label", "property", "utility", "kind", "rate", "minimum_charge", "archived_at"]
    list_filter = ["kind"]
    search_fields = ["label", "serial", "property__name"]
    raw_id_fields = ["organization", "property", "created_by", "archived_by"]
    inlines = [MeterUnitInline]


@admin.register(MeterReading)
class MeterReadingAdmin(admin.ModelAdmin):
    list_display = ["meter", "read_on", "value", "status", "is_baseline", "flags"]
    list_filter = ["status", "is_baseline"]
    raw_id_fields = ["organization", "meter", "recorded_by"]
    readonly_fields = ["previous", "flags", "status", "rate", "minimum_charge", "approved_at", "approved_by",
                       "approval_note", "rejected_at", "rejected_by", "reject_reason"]


@admin.register(MeterCharge)
class MeterChargeAdmin(admin.ModelAdmin):
    list_display = ["description", "lease", "amount", "billing_month", "cancelled_at"]
    raw_id_fields = ["organization", "reading", "lease", "unit"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Scan)
class ScanAdmin(admin.ModelAdmin):
    """Photos read by the model (D-144). Only what it cost is kept; the photo and its text are not."""

    list_display = ["created_at", "organization", "kind", "status", "found", "input_tokens", "output_tokens", "cost"]
    list_filter = ["kind", "status"]
    search_fields = ["organization__name"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
