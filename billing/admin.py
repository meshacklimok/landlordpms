from django.contrib import admin

from .models import ChargeType


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
