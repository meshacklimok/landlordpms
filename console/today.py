"""One page of what needs the platform owner's attention today, each part shown only to those who may act on it."""

import datetime

from django.utils import timezone

from accounts.models import Organization
from support.models import SupportRequest

from . import alerts, metrics, owners, problems

LIMIT = 6


def build(allowed: list[str], *, superuser: bool = False, now: datetime.datetime | None = None) -> dict:
    now = now or timezone.now()
    today = timezone.localdate(now)
    out: dict = {"sections": 0}
    if "support" in allowed:
        waiting = (SupportRequest.objects.filter(first_reply_at__isnull=True)
                   .exclude(status=SupportRequest.Status.CLOSED).select_related("organization").order_by("created_at"))
        out["support"] = {"count": waiting.count(), "items": list(waiting[:LIMIT]),
                          "late": waiting.filter(created_at__lt=now - datetime.timedelta(hours=24)).count()}
    if "health" in allowed:
        data = problems.queue(now)
        out["problems"] = {"messages": data["message_count"], "webhooks": data["webhook_count"],
                           "mpesa": data["mpesa_count"], "total": problems.total(data), "lines": alerts.problems(now)}
    if "organizations" in allowed:
        risky = metrics.watchlist(today)
        out["watch"] = {"count": len(risky), "items": risky[:LIMIT]}
        since = now - datetime.timedelta(days=3)
        out["new_signups"] = list(Organization.objects.filter(created_at__gte=since).order_by("-created_at")[:LIMIT])
    if superuser:
        due = owners.follow_ups(today)
        out["follow_ups"] = {"count": due["due"], "items": (due["overdue"] + due["today"])[:LIMIT]}
    out["sections"] = len([k for k in out if k != "sections"])
    return out
