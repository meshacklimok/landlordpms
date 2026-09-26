from django.contrib import admin

from .models import Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "phone", "status", "organization", "archived_at"]
    list_filter = ["kind", "status"]
    search_fields = ["name", "phone", "organization__name"]
    readonly_fields = ["public_id", "status", "created_at", "updated_at", "archived_at", "archived_by"]

    def get_queryset(self, request):
        return Tenant.all_objects.select_related("organization")

    def has_delete_permission(self, request, obj=None):
        # Archive, never delete (doc 11 §23).
        return False
