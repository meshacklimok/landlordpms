"""Alerts on staff phones through the browser's Web Push (D-089).

A staff member's in-app message is also pushed to the devices where they turned alerts on. The push
carries only the kind of alert and a link, never names or amounts (item 2). Payloads are encrypted
for the device (RFC 8291, aes128gcm) and signed with the server's VAPID key (RFC 8292), using the
`cryptography` library. Senders: `WebPushSender` for real sends, `MemoryPushSender` for tests.
"""

import base64
import datetime
import json
import logging
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.translation import gettext as _

from . import catalog
from .models import Message, PushSubscription

logger = logging.getLogger(__name__)

# Known push services (item 6): the server only ever calls these.
PUSH_HOSTS = ("fcm.googleapis.com", "push.services.mozilla.com", "notify.windows.com", "push.apple.com")
MAX_DEVICES = 10
TIMEOUT = 10
TTL = 3600
STALE = datetime.timedelta(hours=1)
RECORD_SIZE = 4096
JWT_LIFETIME = 12 * 3600


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def enabled() -> bool:
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def make_keys() -> tuple[str, str]:
    """A new VAPID pair: (public key as the browser wants it, private key), both base64url."""
    private = ec.generate_private_key(ec.SECP256R1())
    raw = private.private_numbers().private_value.to_bytes(32, "big")
    return b64(_public_bytes(private.public_key())), b64(raw)


def _public_bytes(key) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _private_key():
    return ec.derive_private_key(int.from_bytes(unb64(settings.VAPID_PRIVATE_KEY), "big"), ec.SECP256R1())


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


def check_endpoint(url: str) -> str:
    """The device address, if it is https on a known push service (item 6)."""
    parts = urllib.parse.urlsplit(url or "")
    host = (parts.hostname or "").lower()
    hosts = PUSH_HOSTS + tuple(settings.PUSH_ALLOWED_HOSTS)
    if parts.scheme != "https" or parts.port not in (None, 443) or parts.username or parts.password \
            or not any(host == h or host.endswith("." + h) for h in hosts):
        raise ValidationError(_("This browser's push service is not supported."))
    return url


def _check_key(text, size: int) -> str:
    try:
        raw = unb64(text) if isinstance(text, str) and len(text) <= 120 else b""
    except ValueError:
        raw = b""
    if len(raw) != size:
        raise ValidationError(_("This browser sent a key that cannot be used."))
    return text


def subscribe(user, data, *, user_agent: str = "") -> PushSubscription:
    """Registers this device for `user`. A device that was someone else's moves to `user` (a shared phone)."""
    if not enabled():
        raise ValidationError(_("Alerts on phones are not set up."))
    keys = data.get("keys") if isinstance(data, dict) else None
    if not isinstance(keys, dict) or not isinstance(data.get("endpoint"), str):
        raise ValidationError(_("This browser did not send a push address."))
    endpoint = check_endpoint(data["endpoint"])
    if len(endpoint) > 1000:
        raise ValidationError(_("This browser's push service is not supported."))
    p256dh, auth = _check_key(keys.get("p256dh"), 65), _check_key(keys.get("auth"), 16)
    sub, _created = PushSubscription.objects.update_or_create(
        endpoint=endpoint, defaults=dict(user=user, p256dh=p256dh, auth=auth, user_agent=user_agent[:200],
                                         failures=0))
    return sub


def unsubscribe(user, endpoint: str | None = None) -> int:
    """Turns alerts off for this device, or for all of `user`'s devices when `endpoint` is None."""
    qs = PushSubscription.objects.filter(user=user)
    if endpoint is not None:
        qs = qs.filter(endpoint=endpoint)
    return qs.delete()[0]


# ---------------------------------------------------------------------------
# Encryption and signing
# ---------------------------------------------------------------------------


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None, server_key=None) -> bytes:
    """The request body for one device: RFC 8291 with a single aes128gcm record (RFC 8188)."""
    device_public = unb64(p256dh)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    server_public = _public_bytes(server_key.public_key())
    shared = server_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),
                                                                                        device_public))
    ikm = _hkdf(unb64(auth), shared, b"WebPush: info\x00" + device_public + server_public, 32)
    salt = salt or os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    if len(payload) + 17 > RECORD_SIZE:
        raise ValueError("Push payload too long")
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", RECORD_SIZE, len(server_public)) + server_public + ciphertext


def vapid_header(endpoint: str, now: float | None = None) -> str:
    """`Authorization: vapid t=<JWT>, k=<public key>` for the push service of `endpoint` (RFC 8292)."""
    parts = urllib.parse.urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int((now or time.time()) + JWT_LIFETIME),
              "sub": settings.VAPID_SUBJECT}
    signing = (b64(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()) + "."
               + b64(json.dumps(claims, separators=(",", ":")).encode()))
    r, s = decode_dss_signature(_private_key().sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
    token = signing + "." + b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={token}, k={settings.VAPID_PUBLIC_KEY}"


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------


@dataclass
class PushResult:
    status: int  # the push service's HTTP status; 0 when nothing came back


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


class WebPushSender:
    name = "webpush"

    def send(self, sub: PushSubscription, payload: dict, *, urgent: bool = False) -> PushResult:
        body = encrypt(json.dumps(payload, separators=(",", ":")).encode(), sub.p256dh, sub.auth)
        request = urllib.request.Request(check_endpoint(sub.endpoint), data=body, method="POST", headers={  # noqa: S310 - https
            "Authorization": vapid_header(sub.endpoint), "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream", "TTL": str(TTL), "Urgency": "high" if urgent else "normal"})
        try:
            with _opener.open(request, timeout=TIMEOUT) as response:
                return PushResult(response.status)
        except urllib.error.HTTPError as exc:
            return PushResult(exc.code)
        except OSError as exc:
            logger.warning("Push to %s failed: %s", sub.pk, exc)
            return PushResult(0)


class MemoryPushSender:
    """Keeps pushes in a list so tests can read them."""

    name = "memory"
    outbox: list[tuple[str, dict, bool]] = []
    status = 201

    def send(self, sub, payload, *, urgent=False):
        MemoryPushSender.outbox.append((sub.endpoint, payload, urgent))
        return PushResult(MemoryPushSender.status)


def get_sender():
    return import_string(settings.PUSH_BACKEND)()


# ---------------------------------------------------------------------------
# Pushing in-app messages
# ---------------------------------------------------------------------------


def wanted(user) -> bool:
    return enabled() and PushSubscription.objects.filter(user=user).exists()


def payload_for(message: Message) -> dict:
    """Only the kind of alert and a link (item 2): a lock screen is seen by others."""
    return {"title": str(message.get_type_display()), "body": _("Open NyumbaPMS to see it."),
            "url": reverse("notifications:inbox"), "tag": str(message.public_id)}


def push_message(pk: int, now: datetime.datetime | None = None) -> int:
    """Pushes one in-app message to its person's devices, once. Returns how many devices took it."""
    now = now or timezone.now()
    with transaction.atomic():
        message = Message.objects.select_for_update(skip_locked=True).filter(pk=pk, push_due=True).first()
        if message is None:
            return 0
        message.push_due = False
        message.save(update_fields=["push_due", "updated_at"])
    if message.created_at < now - STALE or not enabled():
        return 0
    urgent = catalog.get(message.type).urgent
    payload = payload_for(message)
    sender = get_sender()
    taken = 0
    for sub in PushSubscription.objects.filter(user_id=message.user_id).order_by("-updated_at")[:MAX_DEVICES]:
        try:
            result = sender.send(sub, payload, urgent=urgent)
        except Exception:  # a push service outage must not break the caller
            logger.exception("Push to device %s failed", sub.pk)
            result = PushResult(0)
        if result.status in (404, 410):
            sub.delete()  # the browser dropped this registration
        elif 200 <= result.status < 300:
            taken += 1
            PushSubscription.objects.filter(pk=sub.pk).update(last_pushed_at=now, failures=0)
        else:
            sub.failures += 1
            if sub.failures >= PushSubscription.MAX_FAILURES:
                sub.delete()
            else:
                PushSubscription.objects.filter(pk=sub.pk).update(failures=sub.failures)
    return taken


def send_due(now: datetime.datetime | None = None, limit: int = 500) -> dict[str, int]:
    """Pushes alerts written by bulk jobs (item 5); older ones are dropped without a push."""
    now = now or timezone.now()
    dropped = Message.objects.filter(push_due=True, created_at__lt=now - STALE).update(push_due=False)
    ids = list(Message.objects.filter(push_due=True).order_by("created_at", "pk").values_list("pk", flat=True)[:limit])
    pushed = sum(1 for pk in ids if push_message(pk, now))
    return {"pushed": pushed, "push_dropped": dropped}


SESSION_KEY = "push_endpoint"


def device_is_on(request) -> bool:
    endpoint = request.session.get(SESSION_KEY)
    return bool(endpoint) and PushSubscription.objects.filter(user=request.user, endpoint=endpoint).exists()


def logged_out(sender, request, user, **kwargs):
    """Logging out turns alerts off for that device (item 3): a shared phone shows no one else's alerts."""
    endpoint = request.session.get(SESSION_KEY) if request is not None else None
    if user is not None and endpoint:
        unsubscribe(user, endpoint)
