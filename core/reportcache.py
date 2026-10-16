"""Short-lived cache for the heavy report figures (the dashboard, profit and loss, unit by unit).

The figures, not the page, are cached: the page still renders per request, so menus, CSRF tokens and what the
member may do are never shared. A figure is kept per member (their role decides what they see), per day (the
reports read today's date) and per version of the organization's data. Saving or deleting any row of the
money and property apps bumps that organization's version, so a recorded payment shows on the next load.
Bulk `.update()` calls skip the signal; the timeout covers them. Any cache trouble falls back to computing.
"""

import hashlib
import logging

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.utils import timezone

log = logging.getLogger(__name__)

TIMEOUT = 300  # seconds; the version bump is what keeps figures fresh, this only bounds a missed bump
VERSIONED_APPS = {"accounts", "billing", "payments", "leases", "properties", "tenants", "expenses", "maintenance",
                  "meters", "banking", "mpesa", "reports"}


def _version_key(organization_id) -> str:
    return f"rcv:{organization_id}"


def version(organization_id) -> int:
    return cache.get(_version_key(organization_id)) or 0


def bump(organization_id) -> None:
    key = _version_key(organization_id)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=None)


def _key(membership, name: str, params) -> str:
    digest = hashlib.sha1(repr(params).encode()).hexdigest()[:16]
    return (f"rc:{membership.organization_id}:{version(membership.organization_id)}:{membership.pk}:"
            f"{timezone.localdate().isoformat()}:{name}:{digest}")


def cached(membership, name: str, params, build):
    """Return `build()`, reusing a copy built for the same member, day, version and `params` within TIMEOUT."""
    try:
        key = _key(membership, name, params)
        hit = cache.get(key)
        if hit is not None:
            return hit
    except Exception:  # noqa: BLE001 - a broken cache must never break a report
        log.warning("report cache read failed for %s", name, exc_info=True)
        return build()
    value = build()
    try:
        cache.set(key, value, TIMEOUT)
    except Exception:  # noqa: BLE001 - e.g. a figure that cannot be pickled
        log.warning("report cache write failed for %s", name, exc_info=True)
    return value


def _changed(sender, instance, **kwargs):
    meta = sender._meta
    if meta.app_label not in VERSIONED_APPS:
        return
    org = getattr(instance, "organization_id", None)
    if org is not None:
        bump(org)


def connect() -> None:
    post_save.connect(_changed, dispatch_uid="reportcache_save")
    post_delete.connect(_changed, dispatch_uid="reportcache_delete")
