from django.contrib import admin

from .models import AuditEvent


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ["created_at", "organization", "actor", "action", "object_type", "object_repr", "ip"]
    list_filter = ["action", "object_type"]
    search_fields = ["action", "object_id", "object_repr", "actor__phone", "organization__name"]
    date_hierarchy = "created_at"
    readonly_fields = [f.name for f in AuditEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
