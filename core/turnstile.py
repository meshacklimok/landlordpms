"""Cloudflare Turnstile: a check that a person, not a script, is submitting a public form.

Off while TURNSTILE_SITE_KEY / TURNSTILE_SECRET_KEY are empty (development, tests). Used on the pages anyone can post to
without signing in: sign-up, password reset and the demo request. If Cloudflare cannot be reached the form is let
through and the failure logged: their outage must not stop a landlord from signing up, and the per-address limits
and honeypot still apply.
"""

import json
import logging
import urllib.parse
import urllib.request

from django.conf import settings

from .net import client_ip

log = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
FIELD = "cf-turnstile-response"


def enabled() -> bool:
    return bool(getattr(settings, "TURNSTILE_SITE_KEY", "") and getattr(settings, "TURNSTILE_SECRET_KEY", ""))


def passes(request) -> bool:
    if not enabled():
        return True
    token = request.POST.get(FIELD, "")
    if not token:
        return False
    body = urllib.parse.urlencode({"secret": settings.TURNSTILE_SECRET_KEY, "response": token,
                                   "remoteip": client_ip(request)}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(VERIFY_URL, data=body), timeout=5) as reply:  # noqa: S310
            return bool(json.load(reply).get("success"))
    except Exception:  # noqa: BLE001 - network error, bad JSON
        log.exception("Turnstile check unavailable; letting the form through")
        return True
