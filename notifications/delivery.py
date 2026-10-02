"""The delivery engine (doc 11 §27, D-032, D-044).

`notify` decides, for one recipient and one event, which channel to use or why nothing is
sent, and writes one `Message`. Sending happens after the caller's transaction commits and
again from `send_due` (the `send_due_messages` command) for messages held back by quiet
hours or waiting to retry a failure.
"""

import datetime
import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Membership, Organization
from accounts.permissions import can
from core.sms import SmsResult, get_sms_sender
from core.whatsapp import get_whatsapp_sender

from . import catalog
from .models import ConsentRecord, Message, NotificationPreference, OrganizationNotificationRule
from .rendering import fields_in, render, template_for

logger = logging.getLogger(__name__)

Status = Message.Status
Skip = Message.SkipReason

# Channels that wake a phone, and so wait for quiet hours to end.
_NOISY = {catalog.SMS, catalog.WHATSAPP}
RETRY_BASE = datetime.timedelta(minutes=5)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    enabled: bool
    channels: tuple[str, ...]
    offsets: tuple[int, ...]
    include_co_tenants: bool


def effective_rule(org: Organization, ntype: catalog.NotificationType) -> Rule:
    row = OrganizationNotificationRule.objects.filter(organization=org, type=ntype.codename).first()
    if row is None:
        return Rule(ntype.enabled, ntype.channels, ntype.offsets, False)
    return Rule(
        enabled=row.enabled or ntype.mandatory,
        channels=tuple(row.channels) or ntype.channels,
        offsets=tuple(row.offsets) if row.offsets else ntype.offsets,
        include_co_tenants=row.include_co_tenants,
    )


def sms_available(org: Organization) -> bool:
    """Whether the organization can send SMS: its SMS wallet is in credit (D-060 item 10)."""
    from subscriptions import services as billing

    return billing.sms_available(org)


def whatsapp_available(org: Organization) -> bool:
    return bool(getattr(settings, "WHATSAPP_BACKEND", ""))


# ---------------------------------------------------------------------------
# Recipient checks
# ---------------------------------------------------------------------------


def _recipient_filter(tenant, user) -> dict:
    return {"tenant": tenant} if tenant is not None else {"user": user}


def opted_out(org, ntype: catalog.NotificationType, channel: str, *, tenant=None, user=None) -> bool:
    """A row for this type beats a row for every type; no row means not opted out."""
    rows = {p.type: p.enabled for p in NotificationPreference.objects.filter(
        organization=org, channel=channel, type__in=[ntype.codename, ""], **_recipient_filter(tenant, user))}
    if ntype.codename in rows:
        return not rows[ntype.codename]
    return "" in rows and not rows[""]


def consent(org, channel: str, *, tenant=None, user=None) -> bool | None:
    """The latest grant (True) or revocation (False) for the channel, or None if never recorded."""
    latest = ConsentRecord.objects.filter(organization=org, channel=channel,
                                          **_recipient_filter(tenant, user)).order_by("-created_at", "-pk").first()
    return None if latest is None else latest.granted


def _address(channel: str, tenant, user, phone: str = "") -> str:
    if phone:
        return phone if channel == catalog.SMS else ""
    person = tenant if tenant is not None else user
    if channel in (catalog.SMS, catalog.WHATSAPP):
        return person.phone or ""
    if channel == catalog.EMAIL:
        return person.email or ""
    return ""


def _phone_blocked(org, ntype, channel: str, phone: str) -> str | None:
    """A bare number (e.g. an M-Pesa payer): SMS only, and not if a tenant with that number stopped it."""
    from tenants.models import Tenant

    if channel != catalog.SMS:
        return Skip.CHANNEL_UNAVAILABLE
    if not sms_available(org):
        return Skip.CHANNEL_UNAVAILABLE
    if not ntype.mandatory:
        for tenant in Tenant.all_objects.filter(organization=org, phone=phone):
            if opted_out(org, ntype, channel, tenant=tenant):
                return Skip.OPTED_OUT
            if consent(org, channel, tenant=tenant) is False:
                return Skip.NO_CONSENT
    return None


def _blocked(org, ntype, channel: str, tenant, user, phone: str = "") -> str | None:
    """Why this channel cannot be used for this recipient, or None if it can."""
    if phone:
        return _phone_blocked(org, ntype, channel, phone)
    if channel == catalog.IN_APP and user is None:
        return Skip.CHANNEL_UNAVAILABLE  # tenants have no in-app inbox until the portal (Phase 7)
    if channel == catalog.SMS and not sms_available(org):
        return Skip.CHANNEL_UNAVAILABLE
    if channel == catalog.WHATSAPP and not whatsapp_available(org):
        return Skip.CHANNEL_UNAVAILABLE
    if channel != catalog.IN_APP and not _address(channel, tenant, user):
        return Skip.NO_ADDRESS
    if not ntype.mandatory:
        if opted_out(org, ntype, channel, tenant=tenant, user=user):
            return Skip.OPTED_OUT
        given = consent(org, channel, tenant=tenant, user=user)
        # SMS, email and in-app are implied by the tenancy or staff role; WhatsApp needs a grant (D-044 item 3).
        if given is False or (channel == catalog.WHATSAPP and given is not True):
            return Skip.NO_CONSENT
    return None


# ---------------------------------------------------------------------------
# Quiet hours
# ---------------------------------------------------------------------------


def quiet_until(org: Organization, now: datetime.datetime) -> datetime.datetime | None:
    """When quiet hours end if `now` falls inside them, else None."""
    start, end = org.quiet_hours_start, org.quiet_hours_end
    if start == end:
        return None
    local = timezone.localtime(now)
    t = local.time()
    inside = (t >= start or t < end) if start > end else (start <= t < end)
    if not inside:
        return None
    day = local.date() if t < end else local.date() + datetime.timedelta(days=1)
    return datetime.datetime.combine(day, end, tzinfo=local.tzinfo)


# ---------------------------------------------------------------------------
# notify
# ---------------------------------------------------------------------------


def _choose(org, ntype, rule: Rule, tenant, user, language: str,
            phone: str = "") -> tuple[tuple[str, str] | None, str | None, str | None]:
    """The first allowed channel of the rule with its text; or None, the reason nothing can go and its channel.

    The reason shown is the first one that is about the recipient: a tenant who stopped SMS reads
    "no consent", not "WhatsApp not available" (D-044 item 16).
    """
    if not rule.enabled:
        return None, Skip.RULE_DISABLED, None
    reasons = []
    for channel in rule.channels:
        reason = _blocked(org, ntype, channel, tenant, user, phone)
        text = None if reason else template_for(org, ntype, channel, language)
        if reason is None and not text:
            reason = Skip.NO_TEMPLATE
        if reason is None:
            return (channel, text), None, None
        reasons.append((reason, channel))
    if not reasons:
        return None, None, None
    reason, channel = next((r for r in reasons if r[0] != Skip.CHANNEL_UNAVAILABLE), reasons[0])
    return None, reason, channel


def whatsapp_call(ntype: catalog.NotificationType, text: str, language: str, context: dict) -> dict:
    """The approved template and its parameters: the `{field}` values in order, each on one line."""
    params = []
    for name in fields_in(text):
        value = context.get(name)
        params.append(" ".join(("" if value is None else str(value)).split()) or "-")
    return {"name": catalog.whatsapp_template(ntype), "language": catalog.whatsapp_language(ntype, language),
            "params": params}


def preview(org: Organization, type_codename: str, *, tenant=None, user=None,
            rule: Rule | None = None) -> tuple[tuple[str, str] | None, str | None]:
    """What `notify` would do for this recipient, without writing anything: (channel, text) or a skip reason."""
    ntype = catalog.get(type_codename)
    language = getattr(tenant, "language", "") or catalog.EN
    chosen, reason, _channel = _choose(org, ntype, rule or effective_rule(org, ntype), tenant, user, language)
    return chosen, None if chosen else (reason or Skip.CHANNEL_UNAVAILABLE)


def notify(org: Organization, type_codename: str, *, tenant=None, user=None, phone: str = "",
           context: dict | None = None,
           dedupe_key: str = "", lease=None, invoice=None, payment=None, announcement=None, created_by=None,
           urgent: bool = False, now: datetime.datetime | None = None, send_now: bool = True) -> Message | None:
    """Writes one Message for this recipient and event, and sends it after commit if it is due.

    Bulk triggers pass `send_now=False` and leave the message to `send_due`, so a run over many
    leases does not wait on the SMS provider. Returns None when `dedupe_key` was already used in
    this organization: the event was handled. `urgent` sends during quiet hours even when the type waits.
    `phone` addresses someone who is neither a tenant nor a user, by SMS only (an M-Pesa payer).
    """
    if [tenant is not None, user is not None, bool(phone)].count(True) != 1:
        raise ValueError("notify() needs exactly one of tenant, user or phone")
    ntype = catalog.get(type_codename)
    if dedupe_key and Message.objects.filter(organization=org, dedupe_key=dedupe_key).exists():
        return None
    now = now or timezone.now()
    rule = effective_rule(org, ntype)
    language = getattr(tenant, "language", "") or catalog.EN
    base = dict(organization=org, type=ntype.codename, tenant=tenant, user=user, language=language,
                lease=lease, invoice=invoice, payment=payment, announcement=announcement, dedupe_key=dedupe_key,
                created_by=created_by)
    context = {"org_name": org.display_name, **(context or {})}

    chosen, reason, reason_channel = _choose(org, ntype, rule, tenant, user, language, phone)
    if chosen is None:
        channel = reason_channel or (rule.channels[0] if rule.channels else ntype.channels[0])
        # A bare number is the only record of who it was for, so it is kept even when skipped.
        fields = dict(channel=channel, to=phone, status=Status.SKIPPED,
                      skip_reason=reason or Skip.CHANNEL_UNAVAILABLE)
    else:
        channel, text = chosen
        fields = dict(channel=channel, to=_address(channel, tenant, user, phone), body=render(text, context))
        if channel == catalog.WHATSAPP:
            fields["provider_template"] = whatsapp_call(ntype, text, language, context)
        if channel == catalog.IN_APP:
            fields.update(status=Status.DELIVERED, sent_at=now, delivered_at=now)
        else:
            fields.update(status=Status.QUEUED,
                          send_after=None if ntype.urgent or urgent or channel not in _NOISY
                          else quiet_until(org, now))
    try:
        with transaction.atomic():
            message = Message.objects.create(**base, **fields)
    except IntegrityError:
        if dedupe_key and Message.objects.filter(organization=org, dedupe_key=dedupe_key).exists():
            return None
        raise
    if send_now and message.status == Status.QUEUED and message.send_after is None:
        transaction.on_commit(lambda: send_one(message.pk))
    return message


def staff_with(org: Organization, capability: str, property=None) -> list:
    """Users who hold `capability` (for `property`, if given): staff recipients by capability (doc 11 §27)."""
    memberships = (Membership.objects.filter(organization=org, is_active=True, user__is_active=True)
                   .select_related("user", "organization", "role"))
    return [m.user for m in memberships if can(m, capability, property)]


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def _send_email(message: Message) -> SmsResult:
    subject = str(catalog.get(message.type).label)
    send_mail(subject, message.body, None, [message.to])
    return SmsResult(ok=True, provider="email")


def _deliver(message: Message) -> SmsResult:
    if message.channel == catalog.SMS:
        return get_sms_sender().send(message.to, message.body)
    if message.channel == catalog.WHATSAPP:
        t = message.provider_template
        return get_whatsapp_sender().send(message.to, t["name"], t["language"], t["params"])
    if message.channel == catalog.EMAIL:
        return _send_email(message)
    return SmsResult(ok=False, provider="", error=f"No sender for {message.channel}")


def send_one(pk: int, now: datetime.datetime | None = None) -> str | None:
    """Sends one queued message if it is due. Returns its new status, or None if it was not taken."""
    now = now or timezone.now()
    with transaction.atomic():
        message = (Message.objects.select_for_update(skip_locked=True)
                   .filter(pk=pk, status=Status.QUEUED).first())
        if message is None or (message.send_after and message.send_after > now):
            return None
        try:
            result = _deliver(message)
        except Exception as e:  # a provider outage must not break the caller or the batch
            logger.exception("Sending message %s failed", message.pk)
            result = SmsResult(ok=False, provider="", error=str(e) or e.__class__.__name__)
        message.attempts += 1
        message.provider = result.provider or message.provider
        if result.ok:
            message.status = Status.SENT
            message.sent_at = now
            message.provider_id = result.provider_id
            message.cost = result.cost
            message.error = ""
            if message.channel == catalog.SMS:
                from subscriptions import services as billing

                billing.charge_message(message)
        else:
            message.error = (result.error or "")[:300]
            if result.permanent or message.attempts >= Message.MAX_ATTEMPTS:
                message.status = Status.FAILED
            else:
                message.send_after = now + RETRY_BASE * (2 ** (message.attempts - 1))
        message.save()
        return message.status


def send_due(now: datetime.datetime | None = None, limit: int = 500) -> dict[str, int]:
    """Sends queued messages whose time has come, oldest first. Run every few minutes."""
    now = now or timezone.now()
    ids = list(Message.objects.filter(status=Status.QUEUED)
               .exclude(send_after__gt=now).order_by("created_at", "pk").values_list("pk", flat=True)[:limit])
    counts = {"sent": 0, "retrying": 0, "failed": 0}
    for pk in ids:
        status = send_one(pk, now)
        if status == Status.SENT:
            counts["sent"] += 1
        elif status == Status.QUEUED:
            counts["retrying"] += 1
        elif status == Status.FAILED:
            counts["failed"] += 1
    return counts
