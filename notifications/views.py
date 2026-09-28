"""Communications pages: message log, notification settings, templates, the bell and tenant opt-out.

Thin views; services do the work. Anything outside the member's reach is a 404, never a 403.
"""

import uuid

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _l
from django.views import View

from accounts.mixins import CapabilityRequiredMixin, OrgMemberRequiredMixin
from accounts.permissions import can

from . import catalog, forms, selectors, services
from .delivery import Rule, effective_rule
from .models import Message
from .rendering import SMS_SEGMENT, template_for
from .rendering import render as fill

PAGE_SIZE = 30
TAB_LABELS = {"waiting": _l("Waiting"), "sent": _l("Sent"), "failed": _l("Failed"), "skipped": _l("Skipped")}

# Example values for template previews.
SAMPLE = {
    "tenant_name": "Wanjiku Kamau", "org_name": "", "unit": "A4", "property": "Riverside Court",
    "invoice_number": "INV-2026-000123", "amount": "KES 15,000.00", "amount_due": "KES 15,000.00",
    "due_date": "05/10/2026", "balance": "KES 15,000.00", "pay_reference": "RIV-A4", "paid_on": "03/10/2026",
    "receipt_number": "RCT-2026-000456", "receipt_link": "https://pms.example.com/r/AbCdEf123456",
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
            base = base.filter(Q(tenant__name__icontains=q) | Q(to__icontains=q) | Q(body__icontains=q))
        if t := filters.get("type"):
            base = base.filter(type=t)
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
            "tenant": tenant, "filtered": bool(filters.get("q") or filters.get("type") or "tenant" in request.GET),
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
            rule = effective_rule(org, ntype)
            rows.append({
                "type": ntype, "rule": rule, "offsets": ", ".join(str(o) for o in rule.offsets),
                "changed": rule != Rule(ntype.enabled, ntype.channels, ntype.offsets, False),
                "error": (errors or {}).get(ntype.codename),
            })
        return rows

    def context(self, request, quiet_form=None, errors=None):
        org = request.organization
        rows = self.rows(org, errors)
        return {
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

        ntype = _type_or_404(request.POST.get("type", ""))
        form = forms.RuleForm(request.POST)
        form.is_valid()
        data = form.cleaned_data
        try:
            if request.POST.get("action") == "reset":
                services.save_rule(m, ntype.codename, enabled=ntype.enabled, offsets=ntype.offsets, request=request)
            else:
                services.save_rule(m, ntype.codename, enabled=data.get("enabled", False) or ntype.mandatory,
                                   offsets=data.get("offsets") or "", channels=effective_rule(m.organization,
                                                                                              ntype).channels,
                                   include_co_tenants=data.get("include_co_tenants", False), request=request)
        except ValidationError as e:
            return render(request, self.template_name,
                          self.context(request, errors={ntype.codename: _errors(e)}), status=400)
        messages.success(request, _("“%(name)s” saved.") % {"name": ntype.label})
        return redirect(f"{request.path}#type-{ntype.codename}")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def _sample(org) -> dict:
    return {**SAMPLE, "org_name": org.display_name}


class TemplateListView(CapabilityRequiredMixin, View):
    template_name = "notifications/templates.html"
    required_capability = "templates.manage"

    def get(self, request):
        org = request.organization
        groups = []
        for ntype in catalog.TYPES:
            items = []
            for channel in services.usable_channels(ntype):
                for language in services.usable_languages(ntype):
                    override = services.live_template(org, ntype.codename, channel, language)
                    text = template_for(org, ntype, channel, language) or ""
                    items.append({"channel": channel, "language": language, "custom": override is not None,
                                  "preview": fill(text, _sample(org))})
            groups.append({"type": ntype, "items": items})
        return render(request, self.template_name, {"groups": groups})


class TemplateEditView(CapabilityRequiredMixin, View):
    template_name = "notifications/template_edit.html"
    required_capability = "templates.manage"

    def _target(self, type_codename, channel, language):
        ntype = _type_or_404(type_codename)
        if channel not in services.usable_channels(ntype) or language not in services.usable_languages(ntype):
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
        qs = services.inbox(request.user, request.organization).select_related("payment", "invoice", "lease")
        page = Paginator(qs.order_by("-created_at", "-pk"), PAGE_SIZE).get_page(request.GET.get("page"))
        items = [{"message": msg, "unread": msg.read_at is None, "link": selectors.link_for(msg)} for msg in page]
        services.mark_read(request.user, request.organization, [i["message"].pk for i in items if i["unread"]])
        return render(request, self.template_name, {"page": page, "items": items, "unread_count": 0})

    def post(self, request):
        services.mark_read(request.user, request.organization)
        return redirect("notifications:inbox")


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
            messages.success(request, _("SMS resumed for %(name)s.") % {"name": tenant.name} if allowed
                             else _("SMS stopped for %(name)s. Required messages still go.") % {"name": tenant.name})
        return redirect("tenants:detail", public_id=tenant.public_id)
