"""Communications pages: message log, announcements, settings, templates, the bell and tenant opt-out.

Thin views; services do the work. Anything outside the member's reach is a 404, never a 403.
"""

import json
import uuid

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _l
from django.views import View

from accounts.mixins import CapabilityRequiredMixin, OrgMemberRequiredMixin
from accounts.permissions import can

from . import (
    announcements,
    catalog,
    compose,
    forms,
    own_accounts,
    push,
    selectors,
    services,
    triggers,
    whatsapp_account,
)
from .delivery import Rule, effective_rule, whatsapp_available
from .models import Message, MessageTemplate, PushSubscription
from .rendering import SMS_MAX, SMS_SEGMENT, template_for
from .rendering import render as fill

PAGE_SIZE = 30
TAB_LABELS = {"waiting": _l("Waiting"), "sent": _l("Sent"), "failed": _l("Failed"), "skipped": _l("Skipped")}

# Example values for template previews.
SAMPLE = {
    "tenant_name": "Wanjiku Kamau", "org_name": "", "unit": "A4", "property": "Riverside Court",
    "invoice_number": "INV-2026-000123", "amount": "KES 15,000.00", "amount_due": "KES 15,000.00",
    "due_date": "05/10/2026", "balance": "KES 15,000.00", "pay_reference": "RIV-A4", "paid_on": "03/10/2026",
    "receipt_number": "RCT-2026-000456", "receipt_link": "https://pms.example.com/r/AbCdEf123456",
    "amount_plain": "8,000.00", "breakdown": "Rent: 8,000.00", "balance_signed": "-8,000.00",
    "text": "Water will be off on Friday from 9am to 1pm.", "recorded_by": "Otieno", "offset": "3",
}


def _type_or_404(codename: str) -> catalog.NotificationType:
    try:
        return catalog.get(codename)
    except ValueError:
        raise Http404 from None


def _errors(exc: ValidationError) -> str:
    return " ".join(exc.messages)


def sms_parts(text: str) -> int:
    """How many SMS a text costs: one up to 160 characters, then 153 per part."""
    n = len(text)
    return 1 if n <= SMS_SEGMENT else -(-n // 153)


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Message log
# ---------------------------------------------------------------------------


class MessageLogView(CapabilityRequiredMixin, View):
    template_name = "notifications/log.html"
    required_capability = "messages.view"

    def get(self, request):
        form = forms.LogFilterForm(request.GET or None)
        filters = form.filters()
        base = selectors.visible_messages(request.membership).select_related("tenant", "lease__unit")
        if q := filters.get("q"):
            base = base.filter(Q(tenant__name__icontains=q) | Q(to__icontains=q) | Q(body__icontains=q)
                               | Q(subject__icontains=q) | Q(provider_id__iexact=q))
        if t := filters.get("type"):
            base = base.filter(type=t)
        if c := filters.get("channel"):
            base = base.filter(channel=c)
        if d := filters.get("date_from"):
            base = base.filter(created_at__date__gte=d)
        if d := filters.get("date_to"):
            base = base.filter(created_at__date__lte=d)
        tenant = None
        if "tenant" in request.GET:
            tenant = selectors.visible_tenant(request.membership, _uuid(request.GET["tenant"]))
            base = base.filter(tenant=tenant) if tenant else base.none()
        status = filters.get("status", "")
        qs = base.filter(status__in=selectors.STATES[status]) if status else base
        page = Paginator(qs.order_by("-created_at", "-pk"), PAGE_SIZE).get_page(request.GET.get("page"))
        m = request.membership
        counts = selectors.counts_by_state(base)
        return render(request, self.template_name, {
            "page": page, "form": form, "filters": filters, "status": status, "counts": counts,
            "tabs": [(key, label, counts[key]) for key, label in TAB_LABELS.items()],
            "tenant": tenant, "filtered": bool(set(filters) - {"status"} or "tenant" in request.GET),
            "can_settings": can(m, "organization.manage"), "can_templates": can(m, "templates.manage"),
        })


class MessageDetailView(CapabilityRequiredMixin, View):
    template_name = "notifications/detail.html"
    required_capability = "messages.view"

    def _get(self, request, public_id) -> Message:
        return get_object_or_404(selectors.visible_messages(request.membership)
                                 .select_related("tenant", "lease__unit__property", "invoice", "payment",
                                                 "created_by"), public_id=public_id)

    def get(self, request, public_id):
        message = self._get(request, public_id)
        return render(request, self.template_name, {
            "message": message, "segments": sms_parts(message.body) if message.channel == catalog.SMS else 0,
            "can_retry": message.status == Message.Status.FAILED and can(request.membership, "messages.send"),
        })

    def post(self, request, public_id):
        message = self._get(request, public_id)
        try:
            services.retry_message(request.membership, message, request=request)
        except ValidationError as e:
            messages.error(request, _errors(e))
        else:
            messages.success(request, _("Queued to send again."))
        return redirect("notifications:detail", public_id=message.public_id)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class SettingsView(CapabilityRequiredMixin, View):
    template_name = "notifications/settings.html"
    required_capability = "organization.manage"

    def rows(self, org, errors=None):
        rows = []
        for ntype in catalog.TYPES:
            if ntype.composed:
                continue
            rule = effective_rule(org, ntype)
            offered = self._offered(org, ntype)
            rows.append({
                "type": ntype, "rule": rule, "offsets": ", ".join(str(o) for o in rule.offsets),
                "switches": [{"code": c, "name": c.lower(), "label": dict(catalog.CHANNEL_CHOICES)[c],
                              "on": c in rule.channels,
                              "needs_setup": c == catalog.WHATSAPP and not whatsapp_available(org)}
                             for c in offered],
                "changed": rule != Rule(ntype.enabled, ntype.channels, ntype.offsets, False),
                "error": (errors or {}).get(ntype.codename),
            })
        return rows

    @staticmethod
    def _offered(org, ntype) -> list[str]:
        """The channels a tenant type shows a switch for: the ones it has wording for and the org can use."""
        if ntype.audience != catalog.TENANT:
            return []
        out = []
        for c in (catalog.SMS, catalog.WHATSAPP):
            if c in services.usable_channels(ntype):
                out.append(c)
        if catalog.email_copy(ntype):
            out.append(catalog.EMAIL)
        return out

    def _channels(self, org, ntype, data) -> list[str]:
        """The rule's channels after the switches: those switched on, then what no switch covers."""
        current = effective_rule(org, ntype).channels
        offered = self._offered(org, ntype)
        on = {c for c in offered if data.get(c.lower())}
        if offered and not on and data.get("enabled"):
            raise ValidationError({"channels": _("Choose at least one way to send it, or switch it off.")})
        kept = [c for c in current if c not in offered]
        order = [c for c in ntype.channels if c in on] + [c for c in (catalog.EMAIL,) if c in on]
        return order + kept

    def context(self, request, quiet_form=None, errors=None, reply_form=None):
        org = request.organization
        rows = self.rows(org, errors)
        cfg = triggers.message_settings(org)
        return {
            "content": {"balance_includes_deposit": bool(cfg and cfg.balance_includes_deposit),
                        "balance_shows_credit": bool(cfg and cfg.balance_shows_credit)},
            "reply_form": reply_form or forms.ReplyEmailForm(initial={"reply_email": org.reply_email}),
            "tenant_rows": [r for r in rows if r["type"].audience == catalog.TENANT],
            "staff_rows": [r for r in rows if r["type"].audience == catalog.STAFF],
            "quiet_form": quiet_form or forms.QuietHoursForm(initial={"start": org.quiet_hours_start,
                                                                      "end": org.quiet_hours_end}),
            "quiet_off": org.quiet_hours_start == org.quiet_hours_end,
        }

    def get(self, request):
        return render(request, self.template_name, self.context(request))

    def post(self, request):
        m = request.membership
        if request.POST.get("action") == "quiet":
            form = forms.QuietHoursForm(request.POST)
            if not form.is_valid():
                return render(request, self.template_name, self.context(request, quiet_form=form), status=400)
            services.set_quiet_hours(m, start=form.cleaned_data["start"], end=form.cleaned_data["end"],
                                     request=request)
            messages.success(request, _("Quiet hours saved."))
            return redirect("notifications:settings")

        if request.POST.get("action") == "content":
            form = forms.MessageContentForm(request.POST)
            form.is_valid()
            services.set_message_content(m, includes_deposit=form.cleaned_data.get("balance_includes_deposit", False),
                                         shows_credit=form.cleaned_data.get("balance_shows_credit", False),
                                         request=request)
            messages.success(request, _("What messages show is saved."))
            return redirect("notifications:settings")

        if request.POST.get("action") == "reply_email":
            form = forms.ReplyEmailForm(request.POST)
            if not form.is_valid():
                return render(request, self.template_name, self.context(request, reply_form=form), status=400)
            services.set_reply_email(m, form.cleaned_data["reply_email"], request=request)
            messages.success(request, _("Reply-to address saved."))
            return redirect("notifications:settings")

        ntype = _type_or_404(request.POST.get("type", ""))
        form = forms.RuleForm(request.POST)
        form.is_valid()
        data = form.cleaned_data
        try:
            if request.POST.get("action") == "reset":
                services.save_rule(m, ntype.codename, enabled=ntype.enabled, offsets=ntype.offsets, request=request)
            else:
                channels = self._channels(m.organization, ntype, data)
                services.save_rule(m, ntype.codename, enabled=data.get("enabled", False) or ntype.mandatory,
                                   offsets=data.get("offsets") or "", channels=channels,
                                   include_co_tenants=data.get("include_co_tenants", False), request=request)
        except ValidationError as e:
            return render(request, self.template_name,
                          self.context(request, errors={ntype.codename: _errors(e)}), status=400)
        messages.success(request, _("“%(name)s” saved.") % {"name": ntype.label})
        return redirect(f"{request.path}#type-{ntype.codename}")


# ---------------------------------------------------------------------------
# The organization's own WhatsApp number
# ---------------------------------------------------------------------------


class WhatsAppSettingsView(CapabilityRequiredMixin, View):
    template_name = "notifications/whatsapp.html"
    required_capability = "organization.manage"

    def context(self, request, form=None):
        from .management.commands.whatsapp_templates import templates

        org = request.organization
        account = whatsapp_account.account_for(org)
        initial = {"phone_number_id": account.phone_number_id, "enabled": account.enabled} if account else None
        return {
            "account": account, "form": form or forms.WhatsAppAccountForm(initial=initial),
            "webhook_url": request.build_absolute_uri(whatsapp_account.webhook_path(account)) if account else "",
            "platform_number": bool(getattr(settings, "WHATSAPP_BACKEND", "")),
            "templates": templates(),
        }

    def get(self, request):
        return render(request, self.template_name, self.context(request))

    def post(self, request):
        m, action = request.membership, request.POST.get("action", "save")
        try:
            if action == "check":
                account = whatsapp_account.check(m, request=request)
                if account.check_error:
                    messages.error(request, _("Meta did not accept these details: %(why)s")
                                   % {"why": account.check_error})
                else:
                    messages.success(request, _("Connected to %(name)s.") % {
                        "name": account.verified_name or account.display_number or account.phone_number_id})
            elif action == "webhook":
                whatsapp_account.new_webhook(m, request=request)
                messages.success(request, _("New webhook address and verify token. Add them in Meta."))
            elif action == "remove":
                whatsapp_account.remove(m, request=request)
                messages.success(request, _("Your WhatsApp number is removed."))
            else:
                form = forms.WhatsAppAccountForm(request.POST)
                if not form.is_valid():
                    return render(request, self.template_name, self.context(request, form), status=400)
                try:
                    whatsapp_account.save(m, request=request, **form.cleaned_data)
                except ValidationError as e:
                    for field, errors in e.message_dict.items():
                        for error in errors:
                            form.add_error(field if field in form.fields else None, error)
                    return render(request, self.template_name, self.context(request, form), status=400)
                messages.success(request, _("WhatsApp details saved."))
        except ValidationError as e:
            messages.error(request, _errors(e))
        return redirect("notifications:whatsapp")


class _OwnAccountView(CapabilityRequiredMixin, View):
    """Shared by the email and SMS pages: save, test, remove. Secrets are never shown back."""

    template_name = "notifications/own_account.html"
    required_capability = "organization.manage"
    tab = ""
    kind = ""
    secret_field = ""
    form_class = None
    redirect_to = ""
    saved = removed = ""

    def account(self, org): ...
    def initial(self, account) -> dict | None: ...
    def save(self, m, request, data): ...
    def test(self, m, request): ...
    def remove(self, m, request): ...

    def context(self, request, form=None):
        account = self.account(request.organization)
        return {"tab": self.tab, "kind": self.kind, "account": account,
                "form": form or self.form_class(initial=self.initial(account)),
                "secret_saved": bool(account and account.has(self.secret_field))}

    def get(self, request):
        return render(request, self.template_name, self.context(request))

    def post(self, request):
        m, action = request.membership, request.POST.get("action", "save")
        try:
            if action == "test":
                account = self.test(m, request)
                if account.check_error:
                    messages.error(request, _("The test did not go through: %(why)s") % {"why": account.check_error})
                else:
                    messages.success(request, _("Test sent. Check your own inbox or phone."))
            elif action == "remove":
                self.remove(m, request)
                messages.success(request, self.removed)
            else:
                form = self.form_class(request.POST)
                if not form.is_valid():
                    return render(request, self.template_name, self.context(request, form), status=400)
                try:
                    self.save(m, request, form.cleaned_data)
                except ValidationError as e:
                    for field, errors in e.message_dict.items():
                        for error in errors:
                            form.add_error(field if field in form.fields else None, error)
                    return render(request, self.template_name, self.context(request, form), status=400)
                messages.success(request, self.saved)
        except ValidationError as e:
            messages.error(request, _errors(e))
        return redirect(self.redirect_to)


class EmailSettingsView(_OwnAccountView):
    tab, kind, secret_field = "email", "email", "password"
    form_class, redirect_to = forms.EmailAccountForm, "notifications:email"
    saved = _l("Email details saved. Send yourself a test to check them.")
    removed = _l("Your mail server is removed. Email goes out through the platform again.")

    def account(self, org):
        return own_accounts.email_account(org)

    def initial(self, a):
        return {"host": a.host, "port": a.port, "username": a.username, "use_tls": a.use_tls,
                "from_email": a.from_email, "from_name": a.from_name, "enabled": a.enabled} if a else None

    def save(self, m, request, data):
        own_accounts.save_email(m, request=request, **data)

    def test(self, m, request):
        return own_accounts.test_email(m, request=request)

    def remove(self, m, request):
        own_accounts.remove_email(m, request=request)


class SmsSettingsView(_OwnAccountView):
    tab, kind, secret_field = "sms", "sms", "api_key"
    form_class, redirect_to = forms.SmsAccountForm, "notifications:sms"
    saved = _l("SMS details saved. Send yourself a test to check them.")
    removed = _l("Your SMS account is removed. SMS go through the platform and your SMS wallet again.")

    def account(self, org):
        return own_accounts.sms_account(org)

    def context(self, request, form=None):
        return {**super().context(request, form), "not_allowed": not own_accounts.sms_allowed(request.organization)}

    def initial(self, a):
        return {"username": a.username, "sender_id": a.sender_id, "enabled": a.enabled} if a else None

    def save(self, m, request, data):
        own_accounts.save_sms(m, request=request, **data)

    def test(self, m, request):
        return own_accounts.test_sms(m, request=request)

    def remove(self, m, request):
        own_accounts.remove_sms(m, request=request)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def _sample(org) -> dict:
    return {**SAMPLE, "org_name": org.display_name}


class TemplateListView(CapabilityRequiredMixin, View):
    template_name = "notifications/templates.html"
    required_capability = "templates.manage"

    @staticmethod
    def _uses_swahili(org) -> bool:
        """English is the default; Swahili wording is listed once a tenant or a saved text uses it."""
        return (MessageTemplate.objects.filter(organization=org, language=catalog.SW).exists()
                or org.tenants.filter(language=catalog.SW).exists())

    def get(self, request):
        org = request.organization
        show_sw = request.GET.get("lang") == catalog.SW or self._uses_swahili(org)
        groups = []
        for ntype in catalog.TYPES:
            if ntype.composed:
                continue
            items = []
            for channel in services.usable_channels(ntype):
                if channel == catalog.WHATSAPP and not whatsapp_available(org):
                    continue
                for language in services.usable_languages(ntype):
                    if language == catalog.SW and not show_sw:
                        continue
                    override = services.live_template(org, ntype.codename, channel, language)
                    text = template_for(org, ntype, channel, language) or ""
                    items.append({"channel": channel, "language": language, "custom": override is not None,
                                  "editable": channel in services.editable_channels(ntype),
                                  "preview": fill(text, _sample(org))})
            groups.append({"type": ntype, "items": items})
        return render(request, self.template_name, {"groups": groups, "show_sw": show_sw})


class TemplateEditView(CapabilityRequiredMixin, View):
    template_name = "notifications/template_edit.html"
    required_capability = "templates.manage"

    def _target(self, type_codename, channel, language):
        ntype = _type_or_404(type_codename)
        if channel not in services.editable_channels(ntype) or language not in services.usable_languages(ntype):
            raise Http404
        return ntype

    def _render(self, request, ntype, channel, language, form, status=200):
        org = request.organization
        override = services.live_template(org, ntype.codename, channel, language)
        return render(request, self.template_name, {
            "type": ntype, "channel": channel, "language": language, "form": form, "custom": override is not None,
            "default": ntype.default_body(channel, language),
            "language_label": dict(catalog.LANGUAGE_CHOICES)[language],
            "channel_label": dict(catalog.CHANNEL_CHOICES)[channel],
            "sample": _sample(org), "is_sms": channel == catalog.SMS,
        }, status=status)

    def get(self, request, type_codename, channel, language):
        ntype = self._target(type_codename, channel, language)
        text = template_for(request.organization, ntype, channel, language) or ""
        return self._render(request, ntype, channel, language, forms.TemplateForm(initial={"body": text}))

    def post(self, request, type_codename, channel, language):
        ntype = self._target(type_codename, channel, language)
        m = request.membership
        if request.POST.get("action") == "reset":
            services.reset_template(m, ntype.codename, channel, language, request=request)
            messages.success(request, _("Back to the default text."))
            return redirect("notifications:templates")
        form = forms.TemplateForm(request.POST)
        if form.is_valid():
            try:
                services.save_template(m, ntype.codename, channel, language, body=form.cleaned_data["body"],
                                       request=request)
            except ValidationError as e:
                for field, errs in e.message_dict.items():
                    for err in errs:
                        form.add_error(field if field in form.fields else None, err)
            else:
                messages.success(request, _("Text saved."))
                return redirect("notifications:templates")
        return self._render(request, ntype, channel, language, form, status=400)


# ---------------------------------------------------------------------------
# The bell
# ---------------------------------------------------------------------------


class InboxView(OrgMemberRequiredMixin, View):
    """The member's own in-app notifications. Showing them marks them read."""

    template_name = "notifications/inbox.html"

    def get(self, request):
        form = forms.InboxFilterForm(request.GET or None)
        filters = form.filters()
        qs = services.inbox(request.user, request.organization).select_related("payment", "invoice", "lease")
        if q := filters.get("q"):
            qs = qs.filter(body__icontains=q)
        if t := filters.get("type"):
            qs = qs.filter(type=t)
        if filters.get("unread"):
            qs = qs.filter(read_at__isnull=True)
        page = Paginator(qs.order_by("-created_at", "-pk"), PAGE_SIZE).get_page(request.GET.get("page"))
        items = [{"message": msg, "unread": msg.read_at is None, "link": selectors.link_for(msg)} for msg in page]
        services.mark_read(request.user, request.organization, [i["message"].pk for i in items if i["unread"]])
        return render(request, self.template_name, {
            "page": page, "items": items, "unread_count": 0, "form": form, "filters": filters,
            "filtered": bool(filters), "push_offered": push.enabled(),
            "push_public_key": settings.VAPID_PUBLIC_KEY, "push_on_here": push.device_is_on(request),
            "push_devices": PushSubscription.objects.filter(user=request.user).count()})

    def post(self, request):
        services.mark_read(request.user, request.organization)
        return redirect("notifications:inbox")


class PushView(OrgMemberRequiredMixin, View):
    """Alerts on this device on or off (D-089 item 3). JSON from the notifications page; a form for "everywhere"."""

    MAX_BODY = 4096

    def post(self, request):
        if request.content_type != "application/json":
            if request.POST.get("action") == "off_all":
                push.unsubscribe(request.user)
                request.session.pop(push.SESSION_KEY, None)
                messages.success(request, _("Alerts are off on all your devices."))
            return redirect("notifications:inbox")
        if len(request.body) > self.MAX_BODY:
            return JsonResponse({"error": _("Too long.")}, status=413)
        try:
            data = json.loads(request.body)
        except ValueError:
            data = None
        action = data.get("action") if isinstance(data, dict) else None
        if action == "on":
            try:
                sub = push.subscribe(request.user, data.get("subscription"),
                                     user_agent=request.headers.get("User-Agent", ""))
            except ValidationError as e:
                return JsonResponse({"error": " ".join(e.messages)}, status=422)
            request.session[push.SESSION_KEY] = sub.endpoint
            return JsonResponse({"status": "on"})
        if action == "off":
            endpoint = data.get("endpoint") or request.session.get(push.SESSION_KEY)
            if isinstance(endpoint, str):
                push.unsubscribe(request.user, endpoint)
            request.session.pop(push.SESSION_KEY, None)
            return JsonResponse({"status": "off"})
        return JsonResponse({"error": _("Send on or off.")}, status=400)


# ---------------------------------------------------------------------------
# Tenant opt-out
# ---------------------------------------------------------------------------


class TenantChannelView(CapabilityRequiredMixin, View):
    required_capability = "tenants.manage"

    def post(self, request, public_id, channel):
        from tenants.services import visible_tenants

        tenant = get_object_or_404(visible_tenants(request.membership), public_id=public_id)
        form = forms.TenantChannelForm(request.POST)
        form.is_valid()
        allowed = form.cleaned_data.get("allowed", False)
        try:
            services.set_tenant_channel(request.membership, tenant, channel.upper(), allowed=allowed,
                                        note=form.cleaned_data.get("note", ""), request=request)
        except ValidationError as e:
            messages.error(request, _errors(e))
        else:
            done = {
                (catalog.SMS, True): _("SMS resumed for %(name)s."),
                (catalog.SMS, False): _("SMS stopped for %(name)s. Required messages still go."),
                (catalog.WHATSAPP, True): _("%(name)s now gets messages on WhatsApp."),
                (catalog.WHATSAPP, False): _("WhatsApp stopped for %(name)s. Messages go by SMS."),
                (catalog.EMAIL, True): _("Emails resumed for %(name)s."),
                (catalog.EMAIL, False): _("Emails stopped for %(name)s."),
            }.get((channel.upper(), allowed), _("Saved."))
            messages.success(request, done % {"name": tenant.name})
        return redirect("tenants:detail", public_id=tenant.public_id)


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------


def _state_counts() -> dict:
    return {key: Count("messages", filter=Q(messages__status__in=statuses))
            for key, statuses in selectors.STATES.items()}


class AnnouncementListView(CapabilityRequiredMixin, View):
    template_name = "notifications/announcements.html"
    required_capability = "messages.send_bulk"

    def get(self, request):
        qs = (announcements.visible_announcements(request.membership).select_related("created_by")
              .annotate(**_state_counts()))
        page = Paginator(qs.order_by("-created_at", "-pk"), PAGE_SIZE).get_page(request.GET.get("page"))
        return render(request, self.template_name, {"page": page})


class AnnouncementNewView(CapabilityRequiredMixin, View):
    template_name = "notifications/announcement_new.html"
    required_capability = "messages.send_bulk"

    def _render(self, request, form, preview=None, status=200):
        return render(request, self.template_name, {
            "form": form, "preview": preview, "fields": announcements.FIELDS,
            "price": announcements.price_per_part(), "inline_limit": announcements.BULK_INLINE,
            "has_buildings": form.fields["buildings"].queryset.exists(),
            "org_name": request.organization.display_name, "presets": announcements.PRESETS,
        }, status=status)

    def get(self, request):
        template = request.GET.get("template", "")
        initial = {"nonce": uuid.uuid4(), "text": announcements.preset_text(template)}
        if template == "rent_due":  # the reminder starts aimed at tenants whose rent falls due soon
            days = request.GET.get("days", "")
            initial.update(due=True, due_within=int(days) if days.isdigit() and 1 <= int(days) <= 60 else 7)
        form = forms.AnnouncementForm(membership=request.membership, initial=initial)
        return self._render(request, form)

    def post(self, request):
        form = forms.AnnouncementForm(request.POST, membership=request.membership)
        if not form.is_valid():
            return self._render(request, form, status=400)
        data = form.cleaned_data
        try:
            if request.POST.get("action") != "send":
                preview = announcements.preview(request.membership, data["text"], form.audience())
                return self._render(request, form, preview)
            announcement, created = announcements.send(
                request.membership, data["text"], form.audience(), nonce=data["nonce"],
                urgent=data.get("urgent", False), request=request)
        except ValidationError as e:
            if hasattr(e, "error_dict"):
                for field, errs in e.message_dict.items():
                    for err in errs:
                        form.add_error(field if field in form.fields else None, err)
            else:
                form.add_error(None, _errors(e))
            return self._render(request, form, status=400)
        if created:
            messages.success(request, _("Announcement sent to %(n)s tenants.") % {"n": announcement.recipient_count})
        return redirect("notifications:announcement", public_id=announcement.public_id)


class AnnouncementDetailView(CapabilityRequiredMixin, View):
    template_name = "notifications/announcement_detail.html"
    required_capability = "messages.send_bulk"

    def get(self, request, public_id):
        announcement = get_object_or_404(announcements.visible_announcements(request.membership)
                                         .select_related("created_by"), public_id=public_id)
        base = announcement.messages.select_related("tenant", "lease__unit")
        status = request.GET.get("status", "")
        if status not in selectors.STATES:
            status = ""
        qs = base.filter(status__in=selectors.STATES[status]) if status else base
        page = Paginator(qs.order_by("tenant__name", "pk"), PAGE_SIZE).get_page(request.GET.get("page"))
        counts = selectors.counts_by_state(base)
        return render(request, self.template_name, {
            "announcement": announcement, "page": page, "status": status, "counts": counts,
            "tabs": [(key, label, counts[key]) for key, label in TAB_LABELS.items()],
            "cost": base.aggregate(total=Sum("cost"))["total"],
        })


# ---------------------------------------------------------------------------
# A message staff write for one tenant (D-125)
# ---------------------------------------------------------------------------


class ComposeView(CapabilityRequiredMixin, View):
    """Send an invoice, a receipt or a note to one tenant by email, SMS or WhatsApp, in words staff choose.

    `?invoice=`, `?payment=` or `?tenant=` says what it is about; `to` and `channel` pick the tenant and channel.
    """

    template_name = "notifications/compose.html"
    required_capability = "messages.send"

    def _topic(self, request):
        from billing.selectors import visible_invoices
        from payments.selectors import visible_payments

        m = request.membership
        try:
            if invoice_id := _uuid(request.GET.get("invoice")):
                topic = compose.for_invoice(get_object_or_404(visible_invoices(m), public_id=invoice_id))
            elif payment_id := _uuid(request.GET.get("payment")):
                topic = compose.for_payment(get_object_or_404(visible_payments(m), public_id=payment_id))
            elif tenant := selectors.visible_tenant(m, _uuid(request.GET.get("tenant"))):
                topic = compose.for_tenant(m, tenant, welcome=bool(request.GET.get("welcome")))
            else:
                raise Http404
        except ValidationError:
            raise Http404 from None
        if not compose.can_send(m, topic):
            raise Http404
        return topic

    def _choice(self, request, topic, data):
        """The tenant and channel asked for, else the first tenant and the first channel that reaches them."""
        tenants = topic.tenants()
        if not tenants:
            raise Http404
        wanted = _uuid(data.get("to"))
        tenant = next((t for t in tenants if t.public_id == wanted), tenants[0])
        reasons = compose.channel_reasons(request.organization, tenant)
        channel = data.get("channel", "")
        if channel not in compose.CHANNELS:
            channel = next((c for c in compose.CHANNELS if reasons[c] is None), compose.CHANNELS[0])
        return tenants, tenant, reasons, channel

    def _render(self, request, topic, tenants, tenant, reasons, channel, form, status=200):
        labels = dict(catalog.CHANNEL_CHOICES)
        return render(request, self.template_name, {
            "topic": topic, "tenants": tenants, "tenant": tenant, "channel": channel, "form": form,
            "channels": [{"code": c, "label": labels[c],
                          "reason": Message.SkipReason(reasons[c]).label if reasons[c] else ""}
                         for c in compose.CHANNELS],
            "blocked": reasons[channel], "query": self._query(request),
            "org_name": request.organization.display_name,
            "sms_room": SMS_MAX - len(request.organization.display_name) - 1,
        }, status=status)

    @staticmethod
    def _query(request) -> str:
        for key in ("invoice", "payment", "tenant"):
            if request.GET.get(key):
                welcome = "&welcome=1" if key == "tenant" and request.GET.get("welcome") else ""
                return f"{key}={request.GET[key]}{welcome}"
        return ""

    def get(self, request):
        topic = self._topic(request)
        tenants, tenant, reasons, channel = self._choice(request, topic, request.GET)
        subject, text = compose.draft(request.organization, topic, tenant, channel)
        form = forms.ComposeForm(initial={"nonce": uuid.uuid4(), "subject": subject, "text": text})
        return self._render(request, topic, tenants, tenant, reasons, channel, form)

    def post(self, request):
        topic = self._topic(request)
        tenants, tenant, reasons, channel = self._choice(request, topic, request.POST)
        form = forms.ComposeForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                message, created = compose.send(
                    request.membership, topic, tenant, channel, text=data["text"], subject=data["subject"],
                    nonce=data["nonce"], urgent=data["urgent"], request=request)
            except ValidationError as e:
                if hasattr(e, "error_dict"):
                    for field, errs in e.message_dict.items():
                        for err in errs:
                            form.add_error(field if field in form.fields else None, err)
                else:
                    form.add_error(None, _errors(e))
            else:
                if created and message.send_after:
                    messages.success(request, _("It goes to %(name)s at %(time)s, when quiet hours end.") % {
                        "name": tenant.name, "time": timezone.localtime(message.send_after).strftime("%H:%M")})
                elif created:
                    messages.success(request, _("Sending to %(name)s by %(channel)s.") % {
                        "name": tenant.name, "channel": message.get_channel_display()})
                return redirect("notifications:detail", public_id=message.public_id)
        return self._render(request, topic, tenants, tenant, reasons, channel, form, status=400)
