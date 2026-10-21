"""Remembers, for a visitor who has not signed up yet, the referral code and campaign tags on the link they came by
(D-133). Read when they create their organization."""

import re
from urllib.parse import urlsplit

SESSION_KEY = "growth"
_TAG = re.compile(r"[^a-z0-9._-]")
_CODE = re.compile(r"[^A-Z0-9]")


def _tag(value: str) -> str:
    return _TAG.sub("", (value or "").strip().lower())[:60]


def clean_code(value: str) -> str:
    return _CODE.sub("", (value or "").strip().upper())[:12]


def capture(request) -> None:
    """Called on each page view of a visitor who is not signed in. The first campaign wins until they sign up, but a
    new referral code replaces an old one: the last person who invited them is the one they remember."""
    if request.method != "GET" or request.user.is_authenticated:
        return
    get = request.GET
    seen = dict(request.session.get(SESSION_KEY) or {})
    changed = False
    code = clean_code(get.get("ref", ""))
    if code and seen.get("ref") != code:
        seen["ref"] = code
        changed = True
    if "source" not in seen:
        source = _tag(get.get("utm_source", ""))
        host = urlsplit(request.META.get("HTTP_REFERER", "")).hostname or ""
        own = (request.get_host() or "").split(":")[0]
        if source or (host and host != own):
            seen.update(source=source, medium=_tag(get.get("utm_medium", "")),
                        campaign=_tag(get.get("utm_campaign", "")),
                        referrer_host="" if host == own else host[:100], landing=request.path[:100])
            changed = True
    if changed:
        request.session[SESSION_KEY] = seen


class CaptureCampaignMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        capture(request)
        return self.get_response(request)


def is_valid_invite(request) -> bool:
    """Whether the visitor arrived with a code that belongs to someone, so the page can promise the reward."""
    from .models import ReferralCode

    code = seen(request).get("ref")
    return bool(code) and ReferralCode.objects.filter(code=code).exists()


def seen(request) -> dict:
    return dict(request.session.get(SESSION_KEY) or {}) if request is not None else {}
