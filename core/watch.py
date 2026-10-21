"""Counts failed sign-ins, failed two-step codes and Content Security Policy reports per hour, across the
whole platform, so `check_jobs` can tell the operators about an attack (D-108 item 3).

Per-account and per-IP limits stop a single guesser; these counts catch one spread over many accounts or
addresses. They live in the shared cache, like the rate limits, and are lost on a cache restart, which only
delays an alert. The problem line names the threshold and the hour, not the running count, so the alert
email is not sent again on every check while an attack goes on.
"""

import datetime

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

# Alert when an hour has more than this many. Each can be overridden in settings as SECURITY_ALERT_<KIND>_HOUR.
LIMITS = {"signin": 300, "two_step": 100, "csp": 500}
LABELS = {"signin": "failed sign-ins", "two_step": "wrong two-step codes", "csp": "Content Security Policy reports"}
KEEP = 3 * 3600


def _hour(now: datetime.datetime) -> datetime.datetime:
    return timezone.localtime(now).replace(minute=0, second=0, microsecond=0)


def _key(kind: str, hour: datetime.datetime) -> str:
    return f"watch:{kind}:{hour:%Y%m%d%H}"


def limit(kind: str) -> int:
    return getattr(settings, f"SECURITY_ALERT_{kind.upper()}_HOUR", LIMITS[kind])


def count(kind: str, now: datetime.datetime | None = None) -> None:
    """Counts one. Never raises: a cache that is down must not break a login."""
    if kind not in LIMITS:
        raise ValueError(kind)
    key = _key(kind, _hour(now or timezone.now()))
    try:
        if not cache.add(key, 1, timeout=KEEP):
            cache.incr(key)
    except Exception:  # noqa: BLE001, S110 - the count is best effort; the per-account limits still apply
        pass


def seen(kind: str, hour: datetime.datetime) -> int:
    return cache.get(_key(kind, hour)) or 0


def problems(now: datetime.datetime | None = None) -> list[str]:
    """This hour and the last one, so an attack that ends just before the hour is still reported."""
    this = _hour(now or timezone.now())
    lines = []
    for kind, label in LABELS.items():
        for hour in (this - datetime.timedelta(hours=1), this):
            if seen(kind, hour) > limit(kind):
                lines.append(f"security: more than {limit(kind)} {label} in the hour from {hour:%Y-%m-%d %H:00}")
    return lines
