"""Emails the operators when a job is late or failed or the health checks fail (D-062 item 2)."""

import datetime
import hashlib

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.utils import timezone

from . import health, jobs

STATE_KEY = "ops:alert"
REPEAT = datetime.timedelta(hours=6)


def recipients() -> list[str]:
    emails = [e.strip() for e in getattr(settings, "OPS_ALERT_EMAILS", "").split(",") if e.strip()]
    return emails or [email for _name, email in getattr(settings, "ADMINS", [])]


def current_problems(now=None) -> list[str]:
    lines = jobs.problems(now)
    lines += [f"health check failed: {name}" for name, ok in health.checks().items() if not ok]
    return lines


def check_and_alert(now: datetime.datetime | None = None) -> str:
    """Returns what was done: "alert", "all clear", "quiet" (already told) or "ok"."""
    now = now or timezone.now()
    lines = current_problems(now)
    state = cache.get(STATE_KEY) or {}
    to = recipients()
    if lines:
        digest = hashlib.sha256("\n".join(lines).encode()).hexdigest()
        last = state.get("at")
        if state.get("digest") == digest and last and now - last < REPEAT:
            return "quiet"
        if to:
            send_mail("[landlordpms] Problem: " + lines[0][:80], "\n".join(lines), None, to)
        cache.set(STATE_KEY, {"digest": digest, "at": now}, None)
        return "alert"
    if state:
        if to:
            send_mail("[landlordpms] All clear", "Every job and health check is fine again.", None, to)
        cache.delete(STATE_KEY)
        return "all clear"
    return "ok"
