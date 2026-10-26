"""Short-lived cache for the heavy report figures (the dashboard, profit and loss, unit by unit).

The figures, not the page, are cached: the page still renders per request, so menus, CSRF tokens and what the
member may do are never shared. A figure is kept per member (their role decides what they see), per day (the
reports read today's date) and per version of the organization's data. Saving or deleting any row of the
money and property apps bumps that organization's version, so a recorded payment shows on the next load.
Bulk `.update()` calls skip the signal; the timeout covers them.

The bump waits for the transaction to commit (otherwise another request could rebuild the figures from the old
rows under the new version and keep them for five minutes) and happens once per organization per transaction, so
the monthly billing run costs one cache write, not one per invoice. Rows that only record that something was
looked at or polled (STK status checks, Daraja credential edits, invitations, exports) do not bump at all.

When Redis is down the figures are kept in this worker's own memory for a minute instead of being rebuilt on
every visit; each worker has its own copy, so a save shows within that minute rather than at once.
"""

import hashlib
import logging

from django.core.cache import cache, caches
from django.db import connection, transaction
from django.db.models.signals import post_delete, post_save
from django.utils import timezone

log = logging.getLogger(__name__)

TIMEOUT = 300  # seconds; the version bump is what keeps figures fresh, this only bounds a missed bump
VERSIONED_APPS = {"accounts", "billing", "payments", "leases", "properties", "tenants", "expenses", "maintenance",
                  "meters", "banking", "mpesa", "reports", "inspections", "documents", "prospects"}
# Models in those apps that no report reads, or that are written by polling and bookkeeping.
QUIET_MODELS = {"accounts.invitation", "accounts.dataexport", "accounts.otpcode", "mpesa.stkrequest",
                "mpesa.darajacredentials", "mpesa.paylink", "mpesa.codecheck", "mpesa.setupvideo",
                "reports.ownerstatementsend", "reports.monthlyreportsent", "reports.dailysnapshot",
                "maintenance.maintenancephoto", "maintenance.offlinereport", "documents.document"}
# Tests run inside one never-committed transaction, so they switch this off and bump at once.
BUMP_AT_COMMIT = True
FALLBACK_TIMEOUT = 60  # seconds, in this worker's memory, while Redis is unreachable


def _version_key(organization_id) -> str:
    return f"rcv:{organization_id}"


def _local():
    return caches["local"]


def version(organization_id) -> int:
    try:
        return cache.get(_version_key(organization_id)) or 0
    except Exception:  # noqa: BLE001 - Redis down: this worker counts its own saves
        return _local().get(_version_key(organization_id)) or 0


def _incr(store, key) -> None:
    try:
        store.incr(key)
    except ValueError:
        store.set(key, 1, timeout=None)


def bump(organization_id) -> None:
    key = _version_key(organization_id)
    _incr(_local(), key)  # always kept, so the fallback is right the moment Redis stops answering
    try:
        _incr(cache, key)
    except Exception:  # noqa: BLE001
        log.warning("report cache version bump failed", exc_info=True)


def _key(membership, name: str, params) -> str:
    digest = hashlib.sha1(repr(params).encode()).hexdigest()[:16]  # noqa: S324 - a cache key, not a secret
    return (f"rc:{membership.organization_id}:{version(membership.organization_id)}:{membership.pk}:"
            f"{timezone.localdate().isoformat()}:{name}:{digest}")


def cached(membership, name: str, params, build):
    """Return `build()`, reusing a copy built for the same member, day, version and `params` within TIMEOUT."""
    key = None
    try:
        key = _key(membership, name, params)
        hit = cache.get(key)
        if hit is not None:
            return hit
    except Exception:  # noqa: BLE001 - a broken cache must never break a report
        log.warning("report cache read failed for %s", name, exc_info=True)
        return _from_local(key, build)
    value = build()
    try:
        cache.set(key, value, TIMEOUT)
    except Exception:  # noqa: BLE001 - e.g. a figure that cannot be pickled
        log.warning("report cache write failed for %s", name, exc_info=True)
    return value


def _from_local(key, build):
    if key is None:
        return build()
    try:
        hit = _local().get(key)
        if hit is not None:
            return hit
    except Exception:  # noqa: BLE001
        return build()
    value = build()
    try:
        _local().set(key, value, FALLBACK_TIMEOUT)
    except Exception:  # noqa: BLE001, S110 - the figure is still returned
        pass
    return value


def _changed(sender, instance, **kwargs):
    meta = sender._meta
    if meta.app_label not in VERSIONED_APPS or meta.label_lower in QUIET_MODELS:
        return
    org = getattr(instance, "organization_id", None)
    if org is None:
        return
    if not BUMP_AT_COMMIT:
        bump(org)
        return
    # Already waiting to bump this organization in the open transaction (a rollback drops the callback with it).
    if any(getattr(entry[1], "rc_org", None) == org for entry in connection.run_on_commit):
        return

    def flush():
        bump(org)

    flush.rc_org = org
    transaction.on_commit(flush)


def connect() -> None:
    post_save.connect(_changed, dispatch_uid="reportcache_save")
    post_delete.connect(_changed, dispatch_uid="reportcache_delete")
