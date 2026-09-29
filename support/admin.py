"""Incidents for the status page (D-061) and support requests (D-063), worked by Platform Admins."""

from django.contrib import admin
from django.core.cache import cache
from django.http import FileResponse, Http404
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from . import status
from .models import Incident, IncidentUpdate, SupportRequest


class IncidentUpdateInline(admin.TabularInline):
    model = IncidentUpdate
    extra = 1
    fields = ["posted_at", "status", "text"]


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ["title", "impact", "status", "started_at", "resolved_at", "is_published"]
    list_filter = ["status", "impact", "is_published"]
    readonly_fields = ["resolved_at"]
    inlines = [IncidentUpdateInline]

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        cache.delete(status.CACHE_KEY)  # show the change on the status page at once


@admin.register(SupportRequest)
class SupportRequestAdmin(admin.ModelAdmin):
    list_display = ["number", "created_at", "kind", "subject", "organization", "created_by", "status", "file_link"]
    list_filter = ["status", "kind"]
    search_fields = ["number", "subject", "organization__name", "created_by__phone"]
    date_hierarchy = "created_at"
    fields = ["number", "created_at", "organization", "created_by", "kind", "subject", "message", "file_link",
              "page", "user_agent", "status", "internal_note", "closed_at"]
    readonly_fields = ["number", "created_at", "organization", "created_by", "kind", "subject", "message",
                       "file_link", "page", "user_agent", "closed_at"]

    def get_urls(self):
        return [path("<int:pk>/attachment/", self.admin_site.admin_view(self.attachment),
                     name="support_request_attachment"), *super().get_urls()]

    def attachment(self, request, pk):
        item = SupportRequest.objects.filter(pk=pk).first()
        if item is None or not item.attachment:
            raise Http404
        return FileResponse(item.attachment.open("rb"), as_attachment=True,
                            filename=item.attachment_name or item.attachment.name.rsplit("/", 1)[-1])

    @admin.display(description="Attachment")
    def file_link(self, obj):
        if not obj.attachment:
            return "-"
        return format_html('<a href="{}">{}</a>', reverse("admin:support_request_attachment", args=[obj.pk]),
                           obj.attachment_name or "file")

    def save_model(self, request, obj, form, change):
        if obj.status == SupportRequest.Status.CLOSED and obj.closed_at is None:
            obj.closed_at = timezone.now()
        elif obj.status != SupportRequest.Status.CLOSED:
            obj.closed_at = None
        super().save_model(request, obj, form, change)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
