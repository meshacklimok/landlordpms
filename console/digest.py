"""A short morning email to the platform owner: what came in, and what needs attention (D-216)."""

import datetime

from django.core.mail import send_mail
from django.utils import timezone

from core import system_messages
from notifications.providers.mail import SYSTEM, mailer_for
from ops import alerts as ops_alerts
from subscriptions.models import SubscriptionPayment

from . import metrics, owners

KIND = "owner_digest"


def lines(now: datetime.datetime | None = None) -> list[str]:
    from accounts.models import Organization
    from support.models import SupportRequest

    now = now or timezone.now()
    today = timezone.localdate(now)
    since = now - datetime.timedelta(days=1)
    paid = SubscriptionPayment.objects.filter(paid_on__gte=today - datetime.timedelta(days=1))
    money = sum(p.amount for p in paid)
    waiting = SupportRequest.objects.filter(first_reply_at__isnull=True).exclude(status=SupportRequest.Status.CLOSED)
    due = owners.follow_ups(today)
    out = [f"New landlords in the last day: {Organization.objects.filter(created_at__gte=since).count()}",
           f"Subscriptions paid since yesterday: KES {money:,} ({paid.count()} payments)",
           f"Support requests waiting for a first reply: {waiting.count()}",
           f"Landlords on the watch list: {len(metrics.watchlist(today))}",
           f"Follow-ups due or overdue: {due['due']}"]
    problems = ops_alerts.current_problems(now)
    out.append("Problems: " + ("; ".join(problems) if problems else "none"))
    return out


def send(now: datetime.datetime | None = None) -> str:
    """Returns "off" (switched off), "nobody" (no address) or "sent"."""
    if not system_messages.allows(KIND, system_messages.EMAIL):
        return "off"
    to = ops_alerts.recipients()
    if not to:
        return "nobody"
    body = "\n".join(lines(now)) + "\n\nSwitch this email off under Control > System messages."
    send_mail("[NyumbaPMS] Daily summary", body, None, to, using=mailer_for(SYSTEM))
    return "sent"
