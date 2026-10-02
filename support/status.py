"""What the public status page shows (D-061 item 2). Cached so the page cannot load the database."""

import datetime

from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext as _

from core import health, jobs

from .models import Incident

CACHE_KEY = "status:page"
CACHE_SECONDS = 60
RESOLVED_SHOWN = datetime.timedelta(days=14)
OK, DEGRADED, DOWN = "ok", "degraded", "down"


def _incidents(now):
    shown = Incident.objects.filter(is_published=True).prefetch_related("updates")
    open_ = [i for i in shown.exclude(status=Incident.Status.RESOLVED)]
    recent = list(shown.filter(status=Incident.Status.RESOLVED, resolved_at__gte=now - RESOLVED_SHOWN))
    return open_, recent


def build(now: datetime.datetime | None = None) -> dict:
    now = now or timezone.now()
    checks = health.checks()
    states = jobs.job_states(now)
    components = [
        (_("Web app"), OK),
        (_("Database"), OK if checks["database"] else DOWN),
        (_("Cache and sign-in limits"), OK if checks["cache"] else DEGRADED),
        (_("Scheduled jobs: bills, reminders, messages"), OK if all(s == "ok" for s in states.values()) else DEGRADED),
    ]
    open_, recent = _incidents(now)
    if any(state == DOWN for _n, state in components) or any(i.impact == Incident.Impact.OUTAGE for i in open_):
        overall = DOWN
    elif open_ or any(state != OK for _n, state in components):
        overall = DEGRADED
    else:
        overall = OK
    return {"overall": overall, "components": components, "open": open_, "recent": recent, "checked_at": now}


def page() -> dict:
    try:
        data = cache.get(CACHE_KEY)
    except Exception:
        data = None
    if data is None:
        data = build()
        try:
            cache.set(CACHE_KEY, data, CACHE_SECONDS)
        except Exception:
            pass
    return data
