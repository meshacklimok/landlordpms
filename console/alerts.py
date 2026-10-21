"""Things the owner wants on their phone, not only in the console: a pile of failed messages, M-Pesa money nobody
matched for days, a support request nobody answered. Read by ops.alerts.current_problems on every check_jobs run."""

import datetime

from django.conf import settings
from django.utils import timezone

from mpesa.models import MpesaTransaction
from notifications.models import Message
from support.models import SupportRequest


def problems(now: datetime.datetime | None = None) -> list[str]:
    now = now or timezone.now()
    lines = []
    limit = int(getattr(settings, "ALERT_FAILED_MESSAGES", 20))
    failed = Message.objects.filter(status=Message.Status.FAILED, created_at__gte=now - datetime.timedelta(hours=24)).count()
    if limit and failed >= limit:
        lines.append(f"{failed} messages failed to send in the last 24 hours")
    days = int(getattr(settings, "ALERT_UNMATCHED_DAYS", 2))
    stuck = MpesaTransaction.objects.filter(
        status__in=[MpesaTransaction.Status.UNMATCHED, MpesaTransaction.Status.FLAGGED],
        created_at__lt=now - datetime.timedelta(days=days)).count()
    if days and stuck:
        lines.append(f"{stuck} M-Pesa payments have waited over {days} days to be matched")
    hours = int(getattr(settings, "ALERT_SUPPORT_HOURS", 24))
    waiting = SupportRequest.objects.filter(
        status=SupportRequest.Status.OPEN, first_reply_at__isnull=True,
        created_at__lt=now - datetime.timedelta(hours=hours)).count()
    if hours and waiting:
        lines.append(f"{waiting} support requests have had no reply for over {hours} hours")
    return lines
