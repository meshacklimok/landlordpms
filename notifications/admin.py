from django.contrib import admin

from billing.admin import ReadOnlyAdmin

from .models import ConsentRecord, Message, MessageTemplate, NotificationPreference, OrganizationNotificationRule


@admin.register(Message)
class MessageAdmin(ReadOnlyAdmin):
    """The message log is history: never edited or deleted (doc 11 §23)."""

    list_display = ["created_at", "type", "channel", "to", "status", "skip_reason", "attempts", "organization"]
    list_filter = ["status", "channel", "type", "skip_reason"]
    search_fields = ["to", "body", "provider_id", "organization__name"]
    date_hierarchy = "created_at"


@admin.register(ConsentRecord)
class ConsentRecordAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "tenant", "user", "channel", "granted", "source", "organization"]
    list_filter = ["channel", "granted", "source"]


@admin.register(OrganizationNotificationRule)
class OrganizationNotificationRuleAdmin(admin.ModelAdmin):
    list_display = ["organization", "type", "enabled", "channels", "offsets", "include_co_tenants"]
    list_filter = ["type", "enabled"]


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ["organization", "tenant", "user", "type", "channel", "enabled"]
    list_filter = ["channel", "enabled"]


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ["organization", "type", "channel", "language", "archived_at"]
    list_filter = ["type", "channel", "language"]

    def get_queryset(self, request):
        return MessageTemplate.all_objects.all()
