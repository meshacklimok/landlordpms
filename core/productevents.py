"""Product analytics on PostHog (D-140): which features landlords use, where they stop.

Server-side only, so the Content Security Policy stays strict and no script runs in the browser. Events carry
opaque ids (organization id, user id) and counts, never names, phone numbers, emails or amounts. Without
POSTHOG_API_KEY every call does nothing; a failure here must never break a request."""
import logging

from django.conf import settings

log = logging.getLogger(__name__)
_client = None


def _get_client():
    global _client
    if _client is None and settings.POSTHOG_API_KEY:
        import posthog

        _client = posthog.Posthog(settings.POSTHOG_API_KEY, host=settings.POSTHOG_HOST, sync_mode=False,
                                  disable_geoip=True)
    return _client


def capture(event: str, *, user_id, organization_id=None, **props) -> None:
    """Record `event` for a user. `props` must be plain, non-personal values (counts, plan keys, channels)."""
    try:
        client = _get_client()
        if client is None:
            return
        client.capture(event, distinct_id=f"u{user_id}",
                       properties=props, groups={"organization": f"o{organization_id}"} if organization_id else None)
    except Exception:  # noqa: BLE001 - analytics must never break the product
        log.warning("PostHog capture failed for %s", event, exc_info=True)
