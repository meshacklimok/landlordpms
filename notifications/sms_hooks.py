"""What the SMS provider tells us back: delivery reports, replies and opt-outs (D-044 item 14).

These run without a user: the provider calls a secret URL. STOP comes from a phone, not from
an organization, and the shared sender ID cannot tell organizations apart, so a STOP revokes
SMS for every tenant record with that phone and START restores it.
"""

import datetime
import logging

from django.db import transaction
from django.utils import timezone

from audit import services as audit
from core.phone import InvalidPhoneNumber, normalize_phone
from tenants.models import Tenant

from . import catalog
from .models import ConsentRecord, Message
from .services import channel_allowed

logger = logging.getLogger(__name__)

PROVIDER = "africastalking"

# Africa's Talking delivery statuses. Sent, Submitted and Buffered are still on their way.
DELIVERED = {"Success"}
FAILED = {"Failed", "Rejected", "AbsentSubscriber", "Expired"}

STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "ACHA", "SIMAMA"}
START_WORDS = {"START", "UNSTOP", "SUBSCRIBE", "ANZA"}


@transaction.atomic
def delivery_report(provider_id: str, status: str, failure_reason: str = "",
                    now: datetime.datetime | None = None) -> Message | None:
    """Marks a sent message delivered or failed. Unknown ids and in-flight statuses change nothing."""
    if not provider_id:
        return None
    message = (Message.objects.select_for_update()
               .filter(provider=PROVIDER, provider_id=provider_id).first())
    if message is None:
        logger.info("Delivery report for unknown message %s", provider_id)
        return None
    if status in DELIVERED and message.status == Message.Status.SENT:
        message.status = Message.Status.DELIVERED
        message.delivered_at = now or timezone.now()
        message.save(update_fields=["status", "delivered_at", "updated_at"])
    elif status in FAILED and message.status in (Message.Status.SENT, Message.Status.DELIVERED):
        message.status = Message.Status.FAILED
        message.error = f"{status}: {failure_reason}".strip(": ")[:300]
        message.save(update_fields=["status", "error", "updated_at"])
    return message


def _tenants_with(phone: str) -> list[Tenant]:
    try:
        phone = normalize_phone(phone)
    except InvalidPhoneNumber:
        return []
    return list(Tenant.all_objects.filter(phone=phone).select_related("organization"))


@transaction.atomic
def set_channel_from_phone(phone: str, channel: str, *, allowed: bool, note: str,
                           source: str = ConsentRecord.Source.SMS_REPLY, only_restore: bool = False) -> int:
    """Stops (or restarts) a channel for every tenant with this phone. Returns how many changed.

    `only_restore` restarts it only for tenants who agreed to it before: a reply to a shared
    number cannot say which landlord a first grant is meant for (D-044 item 16).
    """
    changed = 0
    for tenant in _tenants_with(phone):
        if channel_allowed(tenant, channel) == allowed:
            continue
        if allowed and only_restore and not ConsentRecord.objects.filter(
                organization=tenant.organization, tenant=tenant, channel=channel, granted=True).exists():
            continue
        ConsentRecord.objects.create(organization=tenant.organization, tenant=tenant, channel=channel,
                                     granted=allowed, source=source, note=note[:200])
        audit.record("tenant.consent", organization=tenant.organization, obj=tenant,
                     changes={channel: [not allowed, allowed], "source": [None, note[:200]]})
        changed += 1
    return changed


def set_sms_from_phone(phone: str, *, allowed: bool, note: str) -> int:
    return set_channel_from_phone(phone, catalog.SMS, allowed=allowed, note=note)


def keyword(text: str) -> str:
    """The first word of a reply, upper case, without trailing punctuation."""
    words = (text or "").strip().upper().split()
    return words[0].strip(".!") if words else ""


def inbound(phone: str, text: str) -> int | None:
    """Handles a reply. STOP-type words opt out, START-type words opt back in; anything else is ignored."""
    word = keyword(text)
    if word in STOP_WORDS:
        return set_sms_from_phone(phone, allowed=False, note=f"Replied {word}")
    if word in START_WORDS:
        return set_sms_from_phone(phone, allowed=True, note=f"Replied {word}")
    return None


def network_opt_out(phone: str) -> int:
    """The network reports the subscriber opted out of our sender ID (bulk SMS opt-out)."""
    return set_sms_from_phone(phone, allowed=False, note="Opted out through the network")
