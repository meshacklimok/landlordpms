"""Staff actions on notifications: rules, quiet hours, templates, tenant opt-outs, retries and the bell.

Every change checks a capability and is audited. Rules and templates store only what differs
from the catalog, so a row that matches the default is removed rather than kept.
"""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import require
from audit import services as audit

from . import catalog
from .delivery import consent, effective_rule, send_one
from .models import ConsentRecord, Message, MessageTemplate, OrganizationNotificationRule
from .rendering import validate_body

MAX_OFFSETS = 3
MAX_OFFSET_DAYS = 60


def _same_org(actor: Membership, obj) -> None:
    if obj.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))


def usable_channels(ntype: catalog.NotificationType) -> list[str]:
    """Channels the type has wording for, in catalog order."""
    have = {channel for channel, _lang in ntype.bodies}
    order = [c for c, _label in catalog.CHANNEL_CHOICES]
    return [c for c in order if c in have]


def editable_channels(ntype: catalog.NotificationType) -> list[str]:
    """WhatsApp wording must match the template Meta approved, so organizations cannot change it (D-044 item 16)."""
    return [c for c in usable_channels(ntype) if c != catalog.WHATSAPP]


def usable_languages(ntype: catalog.NotificationType) -> list[str]:
    """Staff messages are English only until user settings exist (D-044 item 8)."""
    return [catalog.EN] if ntype.audience == catalog.STAFF else list(catalog.LANGUAGES)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def parse_offsets(ntype: catalog.NotificationType, value) -> tuple[int, ...]:
    """'7, 3' → (7, 3). Overdue reminders start on the first overdue day (1)."""
    if isinstance(value, str):
        parts = [p for p in value.replace(";", ",").replace(" ", ",").split(",") if p]
    else:
        parts = list(value or [])
    try:
        days = sorted({int(p) for p in parts}, reverse=True)
    except (TypeError, ValueError):
        raise ValidationError({"offsets": _("Enter whole days separated by commas, e.g. 7, 3.")}) from None
    low = 1 if ntype.codename == "rent_overdue" else 0
    if not days:
        raise ValidationError({"offsets": _("Enter at least one day.")})
    if len(days) > MAX_OFFSETS:
        raise ValidationError({"offsets": _("At most %(n)s reminders.") % {"n": MAX_OFFSETS}})
    if days[-1] < low or days[0] > MAX_OFFSET_DAYS:
        raise ValidationError({"offsets": _("Each day must be between %(low)s and %(high)s.")
                               % {"low": low, "high": MAX_OFFSET_DAYS}})
    return tuple(days)


@transaction.atomic
def save_rule(actor: Membership, type_codename: str, *, enabled: bool, channels=None, offsets=None,
              include_co_tenants: bool = False, request=None) -> OrganizationNotificationRule | None:
    """Stores the organization's change to a type. Returns None when it matches the default (no row kept)."""
    require(actor, "organization.manage")
    org = actor.organization
    ntype = catalog.get(type_codename)
    if ntype.mandatory and not enabled:
        raise ValidationError(_("This notification is required and cannot be switched off."))
    usable = usable_channels(ntype)
    channels = tuple(dict.fromkeys(channels or ntype.channels))
    if not channels or any(c not in usable for c in channels):
        raise ValidationError({"channels": _("Choose from: %(channels)s.") % {"channels": ", ".join(usable)}})
    offsets = parse_offsets(ntype, offsets) if ntype.offsets else ()
    include_co_tenants = bool(include_co_tenants) and ntype.audience == catalog.TENANT

    before = effective_rule(org, ntype)
    row = OrganizationNotificationRule.objects.select_for_update().filter(organization=org, type=ntype.codename).first()
    is_default = (enabled == ntype.enabled and channels == ntype.channels and offsets == tuple(ntype.offsets)
                  and not include_co_tenants)
    if is_default:
        if row is not None:
            row.delete()
        row = None
    else:
        row = row or OrganizationNotificationRule(organization=org, type=ntype.codename)
        row.enabled, row.channels, row.offsets = enabled, list(channels), list(offsets)
        row.include_co_tenants, row.updated_by = include_co_tenants, actor.user
        row.save()
    after = effective_rule(org, ntype)
    changes = audit.diff(
        {"enabled": before.enabled, "channels": list(before.channels), "offsets": list(before.offsets),
         "include_co_tenants": before.include_co_tenants},
        {"enabled": after.enabled, "channels": list(after.channels), "offsets": list(after.offsets),
         "include_co_tenants": after.include_co_tenants})
    if changes:
        audit.record("notifications.rule", actor=actor.user, organization=org, obj=org, request=request,
                     changes={"type": [None, ntype.codename], **changes})
    return row


@transaction.atomic
def set_quiet_hours(actor: Membership, *, start: datetime.time, end: datetime.time, request=None) -> None:
    """Start equal to end switches quiet hours off."""
    require(actor, "organization.manage")
    org = type(actor.organization).objects.select_for_update().get(pk=actor.organization_id)
    before = (org.quiet_hours_start, org.quiet_hours_end)
    if before == (start, end):
        return
    org.quiet_hours_start, org.quiet_hours_end = start, end
    org.save(update_fields=["quiet_hours_start", "quiet_hours_end", "updated_at"])
    actor.organization.quiet_hours_start, actor.organization.quiet_hours_end = start, end
    audit.record("notifications.quiet_hours", actor=actor.user, organization=org, obj=org, request=request,
                 changes={"quiet_hours": [f"{before[0]:%H:%M}–{before[1]:%H:%M}", f"{start:%H:%M}–{end:%H:%M}"]})


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def _check_template_target(ntype, channel: str, language: str) -> None:
    if channel not in editable_channels(ntype) or language not in usable_languages(ntype):
        raise ValidationError(_("That notification has no wording you can change for this channel and language."))


def live_template(org, type_codename: str, channel: str, language: str) -> MessageTemplate | None:
    return MessageTemplate.objects.filter(organization=org, type=type_codename, channel=channel,
                                          language=language).first()


@transaction.atomic
def save_template(actor: Membership, type_codename: str, channel: str, language: str, *, body: str,
                  request=None) -> MessageTemplate | None:
    """Replaces the wording. The old version is archived, not overwritten. Default text removes the override."""
    require(actor, "templates.manage")
    org = actor.organization
    ntype = catalog.get(type_codename)
    _check_template_target(ntype, channel, language)
    body = "\n".join(line.rstrip() for line in (body or "").strip().splitlines())
    if not body:
        raise ValidationError({"body": _("Enter the text.")})
    try:
        validate_body(ntype, channel, body)
    except ValidationError as e:
        raise ValidationError({"body": e.messages}) from None
    old = (MessageTemplate.objects.select_for_update()
           .filter(organization=org, type=ntype.codename, channel=channel, language=language).first())
    default = ntype.bodies.get((channel, language))
    current = old.body if old else default
    if body == current:
        return old
    if old is not None:
        old.archive(actor.user)
    new = None
    if body != default:
        new = MessageTemplate.objects.create(organization=org, type=ntype.codename, channel=channel,
                                             language=language, body=body, updated_by=actor.user)
    audit.record("notifications.template", actor=actor.user, organization=org, obj=new or org, request=request,
                 changes={"template": [None, f"{ntype.codename}/{channel}/{language}"], "body": [current, body]})
    return new


@transaction.atomic
def reset_template(actor: Membership, type_codename: str, channel: str, language: str, *, request=None) -> None:
    require(actor, "templates.manage")
    org = actor.organization
    ntype = catalog.get(type_codename)
    old = (MessageTemplate.objects.select_for_update()
           .filter(organization=org, type=ntype.codename, channel=channel, language=language).first())
    if old is None:
        return
    old.archive(actor.user)
    audit.record("notifications.template", actor=actor.user, organization=org, obj=old, request=request,
                 changes={"template": [None, f"{ntype.codename}/{channel}/{language}"],
                          "body": [old.body, ntype.bodies.get((channel, language))]})


# ---------------------------------------------------------------------------
# Tenant opt-out
# ---------------------------------------------------------------------------


def channel_allowed(tenant, channel: str) -> bool:
    """For service messages SMS is implied by the lease until revoked; WhatsApp needs a grant (D-044 item 3)."""
    given = consent(tenant.organization, channel, tenant=tenant)
    return given is True or (given is None and channel != catalog.WHATSAPP)


@transaction.atomic
def set_tenant_channel(actor: Membership, tenant, channel: str, *, allowed: bool, note: str = "",
                       request=None) -> ConsentRecord | None:
    """Stops or resumes a channel for a tenant, as asked by the tenant. Mandatory messages still go."""
    from tenants.services import visible_tenants

    _same_org(actor, tenant)
    require(actor, "tenants.manage")
    if not visible_tenants(actor).filter(pk=tenant.pk).exists():
        raise PermissionDenied(_("You cannot see this tenant."))
    if channel not in (catalog.SMS, catalog.WHATSAPP, catalog.EMAIL):
        raise ValidationError(_("Unknown channel."))
    if channel_allowed(tenant, channel) == allowed:
        return None
    if allowed and channel == catalog.WHATSAPP and not (note or "").strip():
        raise ValidationError({"note": _("Say how the tenant agreed to WhatsApp messages.")})
    record = ConsentRecord.objects.create(
        organization=tenant.organization, tenant=tenant, channel=channel, granted=allowed,
        source=ConsentRecord.Source.STAFF, note=(note or "").strip()[:200], recorded_by=actor.user)
    audit.record("tenant.consent", actor=actor.user, organization=tenant.organization, obj=tenant, request=request,
                 changes={channel: [not allowed, allowed], **({"note": [None, record.note]} if record.note else {})})
    return record


# ---------------------------------------------------------------------------
# Retry and the bell
# ---------------------------------------------------------------------------


@transaction.atomic
def retry_message(actor: Membership, message: Message, *, request=None) -> Message:
    """Queues a failed message again, with a fresh set of attempts."""
    _same_org(actor, message)
    require(actor, "messages.send")
    message = Message.objects.select_for_update().get(pk=message.pk)
    if message.status != Message.Status.FAILED:
        raise ValidationError(_("Only a failed message can be sent again."))
    message.status, message.attempts, message.send_after, message.error = Message.Status.QUEUED, 0, None, ""
    message.save(update_fields=["status", "attempts", "send_after", "error", "updated_at"])
    audit.record("message.retry", actor=actor.user, organization=message.organization, obj=message, request=request,
                 changes={"status": [Message.Status.FAILED, Message.Status.QUEUED]})
    transaction.on_commit(lambda: send_one(message.pk))
    return message


def inbox(user, org):
    return Message.objects.filter(organization=org, user=user, channel=catalog.IN_APP)


def unread_count(user, org) -> int:
    return inbox(user, org).filter(read_at__isnull=True).count()


def mark_read(user, org, pks=None) -> int:
    qs = inbox(user, org).filter(read_at__isnull=True)
    if pks is not None:
        qs = qs.filter(pk__in=pks)
    return qs.update(read_at=timezone.now())
