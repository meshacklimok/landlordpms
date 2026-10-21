from django.contrib import admin

from .models import NoticeLetter, TenancyLetter


@admin.register(TenancyLetter)
class TenancyLetterAdmin(admin.ModelAdmin):
    """Letters change only through services, so issuing and withdrawing are audited."""

    list_display = ("number", "organization", "lease", "issued_at", "withdrawn_at")
    list_select_related = ("organization", "lease")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(NoticeLetter)
class NoticeLetterAdmin(admin.ModelAdmin):
    """Read only, like tenancy letters: issuing, service and withdrawing go through letters.notices and are audited."""

    list_display = ("number", "kind", "organization", "lease", "issued_at", "served_on", "withdrawn_at")
    list_select_related = ("organization", "lease")

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
