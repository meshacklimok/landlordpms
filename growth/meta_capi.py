"""Meta Conversions API (D-167): tells Meta about a lead or sign-up from our server, so ad reports stay complete when a
browser blocks the Pixel. Sent only for visitors who accepted ad cookies, with phone and email hashed (SHA-256) as Meta
requires; the browser's Pixel sends the same event id, so Meta counts it once. Off without META_PIXEL_ID and
META_CAPI_TOKEN. A failure is logged and never reaches the visitor."""

import hashlib
import json
import logging
import threading
import time
import urllib.request

from django.conf import settings

from core.net import client_ip
from core.phone import InvalidPhoneNumber, normalize_phone

logger = logging.getLogger(__name__)

API = "https://graph.facebook.com/v21.0/{pixel}/events"
CONSENT_COOKIE = "ads-consent"


def enabled() -> bool:
    return bool(getattr(settings, "META_PIXEL_ID", "") and getattr(settings, "META_CAPI_TOKEN", ""))


def _hash(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def _fbc(request, fbclid: str) -> str:
    return request.COOKIES.get("_fbc") or (f"fb.1.{int(time.time() * 1000)}.{fbclid}" if fbclid else "")


def payload(event: str, event_id: str, request, *, phone: str = "", email: str = "", fbclid: str = "") -> dict:
    user = {"client_ip_address": client_ip(request), "client_user_agent": request.META.get("HTTP_USER_AGENT", "")[:300]}
    if email:
        user["em"] = [_hash(email)]
    try:
        if phone:
            user["ph"] = [_hash(normalize_phone(phone).lstrip("+"))]
    except InvalidPhoneNumber:
        pass
    if fbc := _fbc(request, fbclid):
        user["fbc"] = fbc
    if fbp := request.COOKIES.get("_fbp"):
        user["fbp"] = fbp
    body = {"data": [{"event_name": event, "event_time": int(time.time()), "event_id": str(event_id),
                      "action_source": "website", "event_source_url": request.build_absolute_uri(request.path),
                      "user_data": user}],
            "access_token": settings.META_CAPI_TOKEN}
    if code := getattr(settings, "META_TEST_EVENT_CODE", ""):
        body["test_event_code"] = code
    return body


def _post(body: dict) -> None:
    req = urllib.request.Request(API.format(pixel=settings.META_PIXEL_ID), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=8).read()
    except Exception:
        logger.exception("Meta Conversions API call failed")


def send(event: str, event_id, request, **who) -> bool:
    """Queues the event; False when it was not sent (off, or the visitor did not accept ad cookies)."""
    if not enabled() or request is None or request.COOKIES.get(CONSENT_COOKIE) != "yes":
        return False
    threading.Thread(target=_post, args=(payload(event, event_id, request, **who),), daemon=True).start()
    return True
