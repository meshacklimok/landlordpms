"""Google Ads and Meta (Facebook/Instagram) ad tracking (D-165).

Off until an ID is set in settings. Only public pages for visitors who are not signed in run it, plus the one page
after a sign-up, so no tenant or landlord screen ever talks to an ad network. The visitor is asked first: nothing
loads until they accept (static/js/ads.js). The scripts load from our own file because the page policy forbids inline
scripts, and the policy allows the ad networks' addresses only while an ID is set (csp_sources)."""

from django.conf import settings

EVENT_KEY = "ads_event"
LEAD, SIGNUP = "lead", "signup"

_GOOGLE = {
    "script-src": ["https://www.googletagmanager.com"],
    "connect-src": ["https://www.googletagmanager.com", "https://www.google-analytics.com",
                    "https://*.google-analytics.com", "https://*.analytics.google.com", "https://www.google.com",
                    "https://www.google.co.ke", "https://googleads.g.doubleclick.net"],
    "img-src": ["https://www.googletagmanager.com", "https://www.google.com", "https://www.google.co.ke",
                "https://googleads.g.doubleclick.net"],
}
_META = {
    "script-src": ["https://connect.facebook.net"],
    "connect-src": ["https://connect.facebook.net", "https://www.facebook.com"],
    "img-src": ["https://www.facebook.com"],
}


def csp_sources(google_id: str, meta_id: str) -> dict:
    """Extra CSP sources per directive for the networks in use; empty when none is."""
    extra: dict = {}
    for on, sources in ((google_id, _GOOGLE), (meta_id, _META)):
        if on:
            for directive, hosts in sources.items():
                extra.setdefault(directive, []).extend(hosts)
    return extra


def remember_event(request, event: str) -> None:
    """Asks for the next page to report this conversion (a sign-up lands on a signed-in page)."""
    request.session[EVENT_KEY] = event


def context(request) -> dict:
    google_id = getattr(settings, "GOOGLE_ADS_ID", "")
    meta_id = getattr(settings, "META_PIXEL_ID", "")
    if not (google_id or meta_id):
        return {}
    session = getattr(request, "session", None)
    event = session.pop(EVENT_KEY, "") if session is not None and EVENT_KEY in session else ""
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and not event:
        return {}
    labels = {LEAD: getattr(settings, "GOOGLE_ADS_LEAD_LABEL", ""), SIGNUP: getattr(settings, "GOOGLE_ADS_SIGNUP_LABEL", "")}
    return {"ads": {"google_id": google_id, "meta_id": meta_id, "event": event,
                    "label": labels.get(event, "")}}
