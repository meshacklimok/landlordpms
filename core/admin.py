from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.template.response import TemplateResponse

from audit import services as audit

from . import platform_config as pc
from .models import EmailRouting, JobRun, PlatformSetting, SupportContact


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


@admin.register(SupportContact)
class SupportContactAdmin(admin.ModelAdmin):
    """The WhatsApp numbers shown on the site, emails and Help pages."""

    list_display = ["__str__", "whatsapp", "whatsapp_2", "updated_at"]
    fields = ["whatsapp", "whatsapp_2"]

    def changelist_view(self, request, extra_context=None):
        SupportContact.get()
        return super().changelist_view(request, extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit.record("platform.support_contact", actor=request.user, obj=obj,
                     changes={"whatsapp": obj.whatsapp, "whatsapp_2": obj.whatsapp_2})

    def has_add_permission(self, request):
        return not SupportContact.objects.exists()

    def has_delete_permission(self, request, obj=None):
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


def _env_value(key: str) -> str:
    """What the server's environment (or the built-in default) gives, ignoring anything saved in admin."""
    value = getattr(settings._wrapped, key, "")
    return ("1" if value else "") if pc.BY_KEY[key].kind == "bool" else str(value or "")


class PlatformSettingsForm(forms.Form):
    """One field per registered setting. Blank secret = keep; ticking Clear removes a saved value."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        saved = pc.stored()
        for item in pc.REGISTRY:
            if item.kind == "choice":
                field = forms.ChoiceField(choices=[("", "Use the server's setting")] + list(item.choices))
            elif item.kind == "bool":
                field = forms.ChoiceField(choices=[("", "Use the server's setting"), ("1", "Yes"), ("0", "No")])
            elif item.secret:
                field = forms.CharField(widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}))
            else:
                field = forms.CharField(strip=True)
            field.required = False
            field.label = item.label
            field.help_text = item.help
            self.fields[item.key] = field
            if item.secret:
                self.fields["clear_" + item.key] = forms.BooleanField(required=False, label="Clear")
            else:
                self.initial[item.key] = saved.get(item.key, "")
        self.saved = saved

    def effective(self, key: str) -> str:
        """The value the platform would use after this save."""
        typed = self.cleaned_data.get(key, "")
        if pc.BY_KEY[key].secret:
            if self.cleaned_data.get("clear_" + key):
                return _env_value(key)
            return typed or self.saved.get(key) or _env_value(key)
        return typed or _env_value(key)

    def clean(self):
        data = super().clean()
        for item in pc.REGISTRY:
            if item.key in data and (error := pc.validate(item.key, data[item.key])):
                self.add_error(item.key, error)
        if not self.errors:
            self._check_together()
        return data

    def _check_together(self):
        if self.effective("SMS_BACKEND") == pc.SMS_AT:
            for key in ("AT_USERNAME", "AT_API_KEY", "AT_CALLBACK_TOKEN"):
                if not self.effective(key):
                    self.add_error(key, "Needed while the SMS provider is Africa's Talking.")
        if self.effective("SMS_BACKEND") == pc.SMS_CELCOM:
            for key in ("CELCOM_API_KEY", "CELCOM_PARTNER_ID", "CELCOM_SENDER_ID"):
                if not self.effective(key):
                    self.add_error(key, "Needed while the SMS provider is Celcom Africa.")
        if self.effective("WHATSAPP_BACKEND") == pc.WA_CLOUD:
            for key in ("WA_PHONE_NUMBER_ID", "WA_ACCESS_TOKEN", "WA_APP_SECRET", "WA_VERIFY_TOKEN"):
                if not self.effective(key):
                    self.add_error(key, "Needed while WhatsApp is on.")
        if bool(self.effective("VAPID_PUBLIC_KEY")) != bool(self.effective("VAPID_PRIVATE_KEY")):
            self.add_error("VAPID_PRIVATE_KEY", "Set both push keys or neither.")
        if (self.effective("PLATFORM_DARAJA_CONSUMER_KEY") or self.effective("PLATFORM_PAYSTACK_SECRET_KEY")) \
                and len(self.effective("PLATFORM_CALLBACK_TOKEN")) < 32:
            self.add_error("PLATFORM_CALLBACK_TOKEN", "Needed (32+ characters) once Daraja or Paystack is set.")
        if self.effective("PLATFORM_DARAJA_CONSUMER_KEY") and not (
                self.effective("PLATFORM_DARAJA_CONSUMER_SECRET") and self.effective("PLATFORM_DARAJA_PASSKEY")
                and (self.effective("PLATFORM_DARAJA_SHORTCODE") or self.effective("PLATFORM_PAYBILL"))):
            self.add_error("PLATFORM_DARAJA_CONSUMER_KEY",
                           "Daraja also needs the consumer secret, the passkey and a shortcode or Paybill.")


@admin.register(PlatformSetting)
class PlatformSettingAdmin(admin.ModelAdmin):
    """Provider keys and contact details typed here instead of the server's environment file (D-207)."""

    def _owner_only(self, request):
        return request.user.is_active and request.user.is_superuser

    has_module_permission = has_view_permission = has_change_permission = _owner_only

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        if not self._owner_only(request):
            raise PermissionDenied
        if request.method == "POST" and request.POST.get("send_test") in ("sms", "email"):
            self._send_test(request)
            return redirect(request.path)
        form = PlatformSettingsForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            data = form.cleaned_data
            clear = {i.key for i in pc.REGISTRY
                     if (i.secret and data.get("clear_" + i.key)) or (not i.secret and not data[i.key] and i.key in form.saved)}
            changed = pc.save({i.key: data[i.key] for i in pc.REGISTRY}, clear=clear, actor=request.user)
            if changed:
                audit.record("platform.settings", actor=request.user, changes={"keys": sorted(changed)})  # names only
                messages.success(request, f"Saved {len(changed)} setting(s). Other server processes pick them up within a minute.")
            else:
                messages.info(request, "Nothing changed.")
            return redirect(request.path)
        groups = []
        for group in pc.GROUPS:
            rows = []
            for item in (i for i in pc.REGISTRY if i.group == group):
                saved = form.saved.get(item.key)
                if saved:
                    state = "saved here: " + (pc.mask(saved) if item.secret else "shown below")
                elif _env_value(item.key):
                    state = "set in the server environment" if item.secret else "from the server environment"
                else:
                    state = "not set"
                rows.append({"field": form[item.key], "clear": form["clear_" + item.key] if item.secret else None,
                             "state": state})
            groups.append({"name": group, "rows": rows})
        context = {**self.admin_site.each_context(request), "title": "Platform settings", "form": form,
                   "groups": groups, "opts": self.model._meta, "my_email": request.user.email or ""}
        return TemplateResponse(request, "admin/core/platform_settings.html", context)

    def _send_test(self, request):
        """One real SMS or email to the address the owner types, to prove the saved provider works."""
        from core.phone import normalize_phone
        from notifications.email_html import send_branded
        from notifications.providers.mail import SYSTEM, mailer_for
        from notifications.providers.sms import send_sms

        kind = request.POST["send_test"]
        to = request.POST.get("test_" + kind, "").strip()
        if not to:
            messages.error(request, "Type where to send the test.")
            return
        try:
            if kind == "sms":
                phone = normalize_phone(to)
                result = send_sms(phone, "NyumbaPMS test message. Your SMS provider works.")
                if result.ok:
                    messages.success(request, f"Test SMS accepted by {result.provider} for {phone}. Check the phone.")
                else:
                    messages.error(request, f"{result.provider} refused the test SMS: {result.error or 'no reason given'}")
            else:
                send_branded("NyumbaPMS test email", "This is a test. Your email settings work.", [to],
                             using=mailer_for(SYSTEM))
                messages.success(request, f"Test email sent to {to}. Check the inbox and spam.")
        except Exception as exc:  # noqa: BLE001 - show the owner what is wrong, whatever it is
            messages.error(request, f"The test failed: {exc.__class__.__name__}: {str(exc)[:200]}")
        audit.record("platform.test_send", actor=request.user, changes={"kind": kind})
