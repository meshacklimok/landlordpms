from django.contrib import admin

from .models import JobRun


@admin.register(JobRun)
class JobRunAdmin(admin.ModelAdmin):
    """Read-only record of scheduled runs (D-062 item 1, doc 18)."""

    list_display = ["name", "started_at", "finished_at", "ok", "summary"]
    list_filter = ["name", "ok"]
    date_hierarchy = "started_at"
    readonly_fields = ["name", "started_at", "finished_at", "ok", "summary", "error"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
