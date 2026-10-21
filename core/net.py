"""The client's IP address, as seen through our own proxies (doc 14 D14)."""

from django.conf import settings


def client_ip(request) -> str:
    """REMOTE_ADDR, or behind TRUSTED_PROXY_COUNT proxies, the address the outermost one saw.

    Each proxy appends the address it received from to X-Forwarded-For, so the entry n from the
    right was written by our own first proxy. Anything left of it came from the client and is ignored.
    """
    count = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
    if count:
        hops = [h.strip() for h in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if h.strip()]
        if len(hops) >= count:
            return hops[-count]
    return request.META.get("REMOTE_ADDR", "")
