"""Things that failed and can be tried again: messages, webhook deliveries; and money left unmatched (D-179).

The Health page counts them; this queue lists them one by one with a retry, so nothing waits for someone to notice.
"""

import datetime

from django.utils import timezone
from django.utils.translation import gettext as _

from api.models import WebhookDelivery
from audit import services as audit
from mpesa.models import MpesaTransaction
from notifications import delivery
from notifications.models import Message

WINDOW_DAYS = 14  # older failures are history, not a queue: retrying an old reminder would confuse a tenant
STUCK_DAYS = 2
LIMIT = 100


def queue(now=None) -> dict:
    now = now or timezone.now()
    since = now - datetime.timedelta(days=WINDOW_DAYS)
    failed_messages = Message.objects.filter(status=Message.Status.FAILED, created_at__gte=since)
    failed_hooks = WebhookDelivery.objects.filter(status=WebhookDelivery.Status.FAILED, created_at__gte=since)
    stuck = MpesaTransaction.objects.filter(
        status__in=["UNMATCHED", "FLAGGED"], paid_at__lt=now - datetime.timedelta(days=STUCK_DAYS))
    return {
        "messages": list(failed_messages.select_related("organization").order_by("-created_at")[:LIMIT]),
        "message_count": failed_messages.count(),
        "webhooks": list(failed_hooks.select_related("organization", "endpoint").order_by("-created_at")[:LIMIT]),
        "webhook_count": failed_hooks.count(),
        "mpesa": list(stuck.select_related("organization").order_by("paid_at")[:LIMIT]),
        "mpesa_count": stuck.count(),
        "window": WINDOW_DAYS,
    }


def total(data: dict) -> int:
    return data["message_count"] + data["webhook_count"] + data["mpesa_count"]


def retry_message(actor, pk, request=None) -> str | None:
    """Queues a failed message again and sends it now; returns its new status, or None if it is not failed."""
    changed = Message.objects.filter(pk=pk, status=Message.Status.FAILED).update(
        status=Message.Status.QUEUED, attempts=0, send_after=None, error="")
    if not changed:
        return None
    status = delivery.send_one(pk)
    audit.record("console.retry", actor=actor, obj=Message.objects.get(pk=pk), request=request,
                 changes={"message": [None, status or "queued"]})
    return status


def retry_webhook(actor, public_id, request=None) -> str | None:
    from api import webhooks

    d = WebhookDelivery.objects.filter(public_id=public_id, status=WebhookDelivery.Status.FAILED).first()
    if d is None:
        return None
    WebhookDelivery.objects.filter(pk=d.pk).update(
        status=WebhookDelivery.Status.PENDING, attempts=0, next_attempt_at=timezone.now())
    d.refresh_from_db()
    d = webhooks.attempt(d)
    audit.record("console.retry", actor=actor, organization=d.organization, obj=d.endpoint, request=request,
                 changes={"webhook": [None, d.status]})
    return d.status


def retry_all(actor, kind: str, request=None) -> dict[str, int]:
    """Tries every listed failure of one kind again (up to the limit); returns how many went through."""
    data = queue()
    ok = tried = 0
    if kind == "messages":
        for m in data["messages"]:
            tried += 1
            ok += retry_message(actor, m.pk, request=request) in (Message.Status.SENT, Message.Status.DELIVERED)
    elif kind == "webhooks":
        for d in data["webhooks"]:
            tried += 1
            ok += retry_webhook(actor, d.public_id, request=request) == WebhookDelivery.Status.SENT
    return {"tried": tried, "ok": ok}


def describe(text: str) -> str:
    return text or _("No reason given")


__all__ = ["queue", "total", "retry_message", "retry_webhook", "retry_all"]
