from django.contrib import admin

from .models import Property


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "organization", "category", "archived_at"]
    list_filter = ["category"]
    search_fields = ["name", "code", "organization__name"]
    readonly_fields = ["public_id", "created_at", "updated_at", "archived_at", "archived_by"]

    def get_queryset(self, request):
        return Property.all_objects.select_related("organization")

    def has_delete_permission(self, request, obj=None):
        return False
