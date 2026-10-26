from django.contrib import admin

from billing.admin import ReadOnlyAdmin

from . import catalog
from .models import (
    Announcement,
    ConsentRecord,
    EmailAccount,
    EmailSuppression,
    Message,
    MessageTemplate,
    NotificationPreference,
    NotificationTypeSetting,
    OrganizationNotificationRule,
    PlatformWhatsApp,
    SmsAccount,
    WhatsAppAccount,
)


@admin.register(Message)
class MessageAdmin(ReadOnlyAdmin):
    """The message log is history: never edited or deleted (doc 11 §23)."""

    list_display = ["created_at", "type", "channel", "to", "status", "skip_reason", "attempts", "organization"]
    list_filter = ["status", "channel", "type", "skip_reason"]
    search_fields = ["to", "body", "provider_id", "organization__name"]
    date_hierarchy = "created_at"


@admin.register(Announcement)
class AnnouncementAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "summary", "recipient_count", "created_by", "organization"]
    search_fields = ["text", "organization__name"]
    date_hierarchy = "created_at"


@admin.register(ConsentRecord)
class ConsentRecordAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "tenant", "user", "channel", "granted", "source", "organization"]
    list_filter = ["channel", "granted", "source"]


@admin.register(OrganizationNotificationRule)
class OrganizationNotificationRuleAdmin(admin.ModelAdmin):
    list_display = ["organization", "type", "enabled", "channels", "offsets", "include_co_tenants"]
    list_filter = ["type", "enabled"]


@admin.register(NotificationTypeSetting)
class NotificationTypeSettingAdmin(admin.ModelAdmin):
    """What each SMS, WhatsApp and email notification is for, who gets it, how and when, for every organization.

    The same settings are on the superuser console (Notifications)."""

    list_display = ["__str__", "type", "audience", "enabled", "mandatory", "urgent", "channels", "offsets"]
    list_filter = ["enabled", "mandatory", "urgent"]
    search_fields = ["type"]
    readonly_fields = ["audience", "available_channels", "placeholders"]
    fields = ["type", "audience", "available_channels", "placeholders", "enabled", "channels", "offsets",
              "mandatory", "urgent"]

    @admin.display(description="Sent to")
    def audience(self, obj):
        base = catalog.BY_CODENAME.get(obj.type) if obj else None
        return base.audience if base else "-"

    @admin.display(description="Channels this notification has wording for")
    def available_channels(self, obj):
        base = catalog.BY_CODENAME.get(obj.type) if obj else None
        return ", ".join(base.channels) if base else "-"

    @admin.display(description="Fields its wording may use")
    def placeholders(self, obj):
        base = catalog.BY_CODENAME.get(obj.type) if obj else None
        return ", ".join(base.placeholders) if base else "-"

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        catalog.clear_platform_settings()
        from audit import services as audit

        audit.record("platform.notification_type", actor=request.user, obj=obj,
                     changes={"by": "platform admin", "type": obj.type, "fields": sorted(form.changed_data)})

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        catalog.clear_platform_settings()

    def delete_queryset(self, request, queryset):
        super().delete_queryset(request, queryset)
        catalog.clear_platform_settings()


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


# ---------------------------------------------------------------------------
# Accounts that replace the platform's own (D-154). The superuser can edit every one. Secrets are write-only:
# a blank box keeps the saved value, and nothing saved is ever shown.
# ---------------------------------------------------------------------------


def _secret_form(model, secrets: dict[str, str]):
    """A form for `model` with one write-only box per secret (name -> label)."""
    from django import forms

    attrs = {name: forms.CharField(label=label, required=False, strip=True,
                                   widget=forms.PasswordInput(render_value=False,
                                                              attrs={"autocomplete": "new-password"}),
                                   help_text="Leave blank to keep the saved value.")
             for name, label in secrets.items()}

    excluded = [f"{name}_encrypted" for name in secrets] + ["updated_by"]
    attrs["Meta"] = type("Meta", (), {"model": model, "exclude": excluded})

    def save(self, commit=True):
        obj = super(form, self).save(commit=False)
        for name in secrets:
            if self.cleaned_data.get(name):
                obj.set_secret(name, self.cleaned_data[name])
        if commit:
            obj.save()
        return obj

    attrs["save"] = save
    form = type(f"{model.__name__}AdminForm", (forms.ModelForm,), attrs)
    return form


class _AccountAdmin(admin.ModelAdmin):
    secrets: dict[str, str] = {}
    audit_action = ""

    def get_form(self, request, obj=None, **kwargs):
        kwargs["form"] = _secret_form(self.model, self.secrets)
        return super().get_form(request, obj, **kwargs)

    def save_model(self, request, obj, form, change):
        if hasattr(obj, "updated_by_id"):
            obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        from audit import services as audit

        changed = [f for f in form.changed_data] + [s for s in self.secrets if form.cleaned_data.get(s)]
        audit.record(self.audit_action, actor=request.user, organization=getattr(obj, "organization", None), obj=obj,
                     changes={"by": "platform admin", "fields": sorted(set(changed))})  # names only, never values


@admin.register(PlatformWhatsApp)
class PlatformWhatsAppAdmin(_AccountAdmin):
    """The owner's own WhatsApp number, used by every organization that has none. Replaces the WA_* settings."""

    secrets = {"access_token": "Access token", "app_secret": "App secret"}
    audit_action = "platform.whatsapp"
    list_display = ["__str__", "enabled", "updated_at"]

    def changelist_view(self, request, extra_context=None):
        PlatformWhatsApp.get()
        return super().changelist_view(request, extra_context)

    def has_add_permission(self, request):
        return not PlatformWhatsApp.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(WhatsAppAccount)
class WhatsAppAccountAdmin(_AccountAdmin):
    secrets = {"access_token": "Access token", "app_secret": "App secret"}
    audit_action = "platform.whatsapp_account"
    list_display = ["organization", "phone_number_id", "enabled", "checked_at", "check_error"]
    search_fields = ["organization__name", "phone_number_id"]


@admin.register(EmailAccount)
class EmailAccountAdmin(_AccountAdmin):
    secrets = {"password": "Password"}
    audit_action = "platform.email_account"
    list_display = ["organization", "host", "from_email", "enabled", "checked_at", "check_error"]
    search_fields = ["organization__name", "host", "from_email"]


@admin.register(SmsAccount)
class SmsAccountAdmin(_AccountAdmin):
    secrets = {"api_key": "API key"}
    audit_action = "platform.sms_account"
    list_display = ["organization", "username", "sender_id", "enabled", "checked_at", "check_error"]
    search_fields = ["organization__name", "username"]


@admin.register(EmailSuppression)
class EmailSuppressionAdmin(admin.ModelAdmin):
    """Addresses we no longer mail. Delete a row to allow mail to an address again, for example after its owner
    fixed a full mailbox."""

    list_display = ["email", "reason", "provider", "created_at"]
    list_filter = ["reason", "provider"]
    search_fields = ["email"]
    readonly_fields = ["created_at"]
