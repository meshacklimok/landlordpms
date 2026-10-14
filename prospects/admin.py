from django.contrib import admin

from .models import Prospect, ProspectNote, Viewing


class NoteInline(admin.TabularInline):
    model = ProspectNote
    fields = ["at", "kind", "text", "by"]
    readonly_fields = fields
    extra = 0
    can_delete = False


class ViewingInline(admin.TabularInline):
    model = Viewing
    fields = ["scheduled_at", "unit", "status", "outcome", "with_user"]
    readonly_fields = fields
    extra = 0
    can_delete = False


@admin.register(Prospect)
class ProspectAdmin(admin.ModelAdmin):
    """Read only: prospects change through the app so holds and statuses stay consistent and audited."""

    list_display = ["name", "phone", "property", "unit", "status", "assigned_to", "created_at"]
    list_filter = ["status", "source"]
    search_fields = ["name", "phone", "property__name"]
    raw_id_fields = ["organization", "property", "unit", "held_unit", "assigned_to", "tenant", "created_by"]
    inlines = [ViewingInline, NoteInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
