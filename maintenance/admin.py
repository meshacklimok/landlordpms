from django.contrib import admin

from .models import MaintenancePhoto, MaintenanceRequest, MaintenanceUpdate


class UpdateInline(admin.TabularInline):
    model = MaintenanceUpdate
    fields = ["at", "kind", "from_status", "to_status", "text", "shared_with_tenant", "by"]
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(MaintenanceRequest)
class MaintenanceRequestAdmin(admin.ModelAdmin):
    """Read only: requests change through the app so every step is checked and audited."""

    list_display = ["number", "property", "unit", "title", "priority", "status", "due_at"]
    list_filter = ["status", "priority", "kind", "source"]
    search_fields = ["number", "title", "property__name", "unit__code"]
    raw_id_fields = ["organization", "property", "unit", "lease", "tenant", "reported_by", "assigned_to",
                     "supplier", "closed_by"]
    inlines = [UpdateInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(MaintenancePhoto)
class MaintenancePhotoAdmin(admin.ModelAdmin):
    list_display = ["request", "uploaded_by", "uploaded_at", "shared_with_tenant"]
    raw_id_fields = ["request", "uploaded_by"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
