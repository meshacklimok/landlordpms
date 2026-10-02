from django.contrib import admin

from .models import ConditionPhoto, ConditionReport, ConditionReportLine, UnitItem


class ReadOnlyAdmin(admin.ModelAdmin):
    """Reports are evidence: they change only through services, so every change is audited."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UnitItem)
class UnitItemAdmin(ReadOnlyAdmin):
    list_display = ["name", "area", "quantity", "unit", "organization", "archived_at"]
    search_fields = ["name", "unit__code", "organization__name"]

    def get_queryset(self, request):
        return UnitItem.all_objects.select_related("unit", "organization")


class LineInline(admin.TabularInline):
    model = ConditionReportLine
    fields = ["area", "name", "quantity", "condition", "notes"]
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(ConditionReport)
class ConditionReportAdmin(ReadOnlyAdmin):
    list_display = ["public_id", "kind", "status", "unit", "lease", "inspected_on", "organization"]
    list_filter = ["kind", "status"]
    search_fields = ["unit__code", "lease__number", "organization__name"]
    inlines = [LineInline]
    list_select_related = ["unit", "lease", "organization"]


@admin.register(ConditionPhoto)
class ConditionPhotoAdmin(ReadOnlyAdmin):
    list_display = ["public_id", "report", "line", "width", "height", "uploaded_at"]
    list_select_related = ["report", "line"]
