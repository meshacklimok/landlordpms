"""What Meta tells us back about WhatsApp: message statuses and replies (D-044 item 16).

One business number serves every organization, so a STOP reply stops WhatsApp for every tenant
with that phone, and START restarts it only where the tenant had agreed before.
"""

import datetime
import logging

from django.db import transaction
from django.utils import timezone

from . import catalog
from .models import ConsentRecord, Message
from .sms_hooks import START_WORDS, STOP_WORDS, keyword, set_channel_from_phone

logger = logging.getLogger(__name__)

DELIVERED = {"delivered", "read"}
FAILED = {"failed"}


@transaction.atomic
def status_update(provider_id: str, status: str, errors: list | None = None,
                  now: datetime.datetime | None = None) -> Message | None:
    """Marks a sent message delivered or failed. Unknown ids and "sent" change nothing."""
    if not provider_id:
        return None
    message = (Message.objects.select_for_update()
               .filter(channel=catalog.WHATSAPP, provider_id=provider_id).first())
    if message is None:
        logger.info("WhatsApp status for unknown message %s", provider_id)
        return None
    if status in DELIVERED and message.status == Message.Status.SENT:
        message.status = Message.Status.DELIVERED
        message.delivered_at = now or timezone.now()
        message.save(update_fields=["status", "delivered_at", "updated_at"])
    elif status in FAILED and message.status in (Message.Status.SENT, Message.Status.DELIVERED):
        first = (errors or [{}])[0] or {}
        message.status = Message.Status.FAILED
        message.error = f"{first.get('code', '')} {first.get('title', '')}".strip()[:300] or "failed"
        message.save(update_fields=["status", "error", "updated_at"])
    return message


def reply(phone: str, text: str) -> int | None:
    """STOP-type words stop WhatsApp; START-type words restart it where the tenant agreed before."""
    word = keyword(text)
    source = ConsentRecord.Source.WA_REPLY
    if word in STOP_WORDS:
        return set_channel_from_phone(phone, catalog.WHATSAPP, allowed=False, note=f"Replied {word}",
                                      source=source)
    if word in START_WORDS:
        return set_channel_from_phone(phone, catalog.WHATSAPP, allowed=True, note=f"Replied {word}",
                                      source=source, only_restore=True)
    return None


def _reply_text(message: dict) -> str:
    kind = message.get("type")
    if kind == "text":
        return (message.get("text") or {}).get("body", "")
    if kind == "button":
        return (message.get("button") or {}).get("text", "")
    return ""


def process(payload: dict) -> None:
    """Handles one webhook delivery: any number of statuses and replies."""
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            for s in value.get("statuses") or []:
                status_update(str(s.get("id") or ""), str(s.get("status") or ""), s.get("errors"))
            for m in value.get("messages") or []:
                phone = str(m.get("from") or "")
                if phone:
                    reply("+" + phone.lstrip("+"), _reply_text(m))
