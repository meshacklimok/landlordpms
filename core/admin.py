from django.conf import settings
from django.contrib import admin

from audit import services as audit

from .models import EmailRouting, JobRun


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


@admin.register(EmailRouting)
class EmailRoutingAdmin(admin.ModelAdmin):
    """Swaps which mail account (Resend or Brevo) sends which mail. The logins stay in the environment."""

    list_display = ["__str__", "system_account", "messages_account", "updated_at"]
    fields = ["system_account", "messages_account", "configured"]
    readonly_fields = ["configured"]

    @admin.display(description="Accounts set up on the server")
    def configured(self, obj):
        have = set(settings.MAILERS)
        return "; ".join(f"{label}: {'yes' if alias in have else 'NOT set up'}"
                         for alias, label in EmailRouting.Account.choices)

    def changelist_view(self, request, extra_context=None):
        EmailRouting.get()
        return super().changelist_view(request, extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit.record("platform.email_routing", actor=request.user, obj=obj,
                     changes={"system_account": obj.system_account, "messages_account": obj.messages_account})

    def has_add_permission(self, request):
        return not EmailRouting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
