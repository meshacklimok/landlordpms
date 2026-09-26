from django.contrib import admin

from .models import Building, Property, Unit


class NoDeleteAdmin(admin.ModelAdmin):
    """Archive, never delete (doc 11 §23)."""

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Property)
class PropertyAdmin(NoDeleteAdmin):
    list_display = ["name", "code", "organization", "category", "county", "archived_at"]
    list_filter = ["category", "county"]
    search_fields = ["name", "code", "organization__name"]
    readonly_fields = ["public_id", "created_at", "updated_at", "archived_at", "archived_by"]

    def get_queryset(self, request):
        return Property.all_objects.select_related("organization")


@admin.register(Building)
class BuildingAdmin(NoDeleteAdmin):
    list_display = ["name", "property", "organization", "archived_at"]
    search_fields = ["name", "property__name", "organization__name"]
    readonly_fields = ["public_id", "created_at", "updated_at", "archived_at", "archived_by"]

    def get_queryset(self, request):
        return Building.all_objects.select_related("organization", "property")


@admin.register(Unit)
class UnitAdmin(NoDeleteAdmin):
    list_display = ["payment_reference", "property", "unit_type", "manual_status", "organization", "archived_at"]
    list_filter = ["unit_type", "manual_status"]
    search_fields = ["code", "payment_reference", "property__name", "organization__name"]
    readonly_fields = ["public_id", "payment_reference", "created_at", "updated_at", "archived_at", "archived_by"]

    def get_queryset(self, request):
        return Unit.all_objects.select_related("organization", "property", "building")
