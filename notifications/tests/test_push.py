"""Alerts on staff phones through Web Push (D-089)."""

import base64
import datetime
import json
import struct
from io import StringIO
from zoneinfo import ZoneInfo

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, make_org
from accounts.tests.test_isolation import login
from notifications import catalog, delivery, push
from notifications.models import Message, PushSubscription
from notifications.push import MemoryPushSender
from support import pwa
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

EAT = ZoneInfo("Africa/Nairobi")
NOON = datetime.datetime(2026, 2, 3, 12, 0, tzinfo=EAT)
LATE = datetime.datetime(2026, 2, 3, 22, 30, tzinfo=EAT)
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@pytest.fixture
def keys(settings):
    public, private = push.make_keys()
    settings.VAPID_PUBLIC_KEY, settings.VAPID_PRIVATE_KEY = public, private
    settings.VAPID_SUBJECT = "mailto:ops@nyumbapms.test"
    return public, private


@pytest.fixture
def device():
    """A browser's side of a subscription: its key pair and auth secret."""
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(serialization.Encoding.X962,
                                               serialization.PublicFormat.UncompressedPoint)
    return private, {"endpoint": ENDPOINT, "keys": {"p256dh": b64(public), "auth": b64(b"0123456789abcdef")}}


@pytest.fixture
def owner():
    return make_org(name="Acme")


def alert(owner, user=None, now=NOON, **kw):
    return delivery.notify(owner.organization, "payment_pending_review", user=user or owner.user, now=now,
                           context={"tenant_name": "Wanjiku", "unit": "A1", "amount": "KES 5,000",
                                    "recorded_by": "Otieno"}, **kw)


def decrypt(body: bytes, device_private, auth: bytes) -> bytes:
    """The browser's side of RFC 8291, written out separately from the code under test."""
    salt, rs, idlen = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
    server_public = body[21:21 + idlen]
    assert rs == 4096 and idlen == 65
    device_public = device_private.public_key().public_bytes(serialization.Encoding.X962,
                                                             serialization.PublicFormat.UncompressedPoint)
    shared = device_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),
                                                                                            server_public))

    def hkdf(s, ikm, info, n):
        return HKDF(algorithm=hashes.SHA256(), length=n, salt=s, info=info).derive(ikm)

    ikm = hkdf(auth, shared, b"WebPush: info\x00" + device_public + server_public, 32)
    plain = AESGCM(hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)).decrypt(
        hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12), body[21 + idlen:], None)
    assert plain.endswith(b"\x02")  # the last (and only) record
    return plain[:-1]


# ---------------------------------------------------------------------------
# Encryption, signing and keys
# ---------------------------------------------------------------------------


def test_a_payload_is_encrypted_for_the_device_only(keys, device):
    private, sub = device
    body = push.encrypt(b'{"title":"Hi"}', sub["keys"]["p256dh"], sub["keys"]["auth"])
    assert b"Hi" not in body
    assert decrypt(body, private, b"0123456789abcdef") == b'{"title":"Hi"}'
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(Exception):  # noqa: B017 - any failure: another device cannot read it
        decrypt(body, other, b"0123456789abcdef")
    with pytest.raises(ValueError):
        push.encrypt(b"x" * 4096, sub["keys"]["p256dh"], sub["keys"]["auth"])


def test_the_vapid_header_is_signed_with_the_server_key(keys):
    public, _private = keys
    header = push.vapid_header(ENDPOINT, now=1_800_000_000)
    token, k = header.removeprefix("vapid t=").split(", k=")
    assert k == public
    head, claims, signature = token.split(".")
    pad = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
    assert json.loads(pad(head)) == {"typ": "JWT", "alg": "ES256"}
    assert json.loads(pad(claims)) == {"aud": "https://fcm.googleapis.com", "exp": 1_800_000_000 + 12 * 3600,
                                       "sub": "mailto:ops@nyumbapms.test"}
    raw = pad(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), pad(public))
    key.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))


def test_the_command_prints_a_usable_key_pair(settings):
    out = StringIO()
    call_command("vapid_keys", stdout=out)
    lines = dict(line.split("=", 1) for line in out.getvalue().split())
    settings.VAPID_PUBLIC_KEY, settings.VAPID_PRIVATE_KEY = lines["VAPID_PUBLIC_KEY"], lines["VAPID_PRIVATE_KEY"]
    assert len(base64.urlsafe_b64decode(lines["VAPID_PUBLIC_KEY"] + "=")) == 65 and push.enabled()
    assert push.vapid_header(ENDPOINT).endswith(lines["VAPID_PUBLIC_KEY"])


def test_only_known_push_services_are_called(settings):
    for good in (ENDPOINT, "https://updates.push.services.mozilla.com/wpush/v2/x",
                 "https://wns2-by3p.notify.windows.com/w/?token=x", "https://web.push.apple.com/abc"):
        assert push.check_endpoint(good) == good
    for bad in ("http://fcm.googleapis.com/x", "https://example.com/x", "https://xfcm.googleapis.com/x",
                "https://fcm.googleapis.com.evil.com/x", "https://fcm.googleapis.com:8443/x",
                "https://me@fcm.googleapis.com/x", "https://10.0.0.1/x", "", "fcm.googleapis.com/x"):
        with pytest.raises(ValidationError):
            push.check_endpoint(bad)
    settings.PUSH_ALLOWED_HOSTS = ["push.example.net"]
    assert push.check_endpoint("https://a.push.example.net/x")


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


def test_a_device_is_registered_checked_and_moves_to_whoever_turned_it_on(owner, keys, device):
    _private, sub = device
    first = push.subscribe(owner.user, sub, user_agent="Chrome on Android")
    assert (first.user, first.endpoint, first.user_agent) == (owner.user, ENDPOINT, "Chrome on Android")
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    moved = push.subscribe(caretaker.user, sub)
    assert moved.pk == first.pk and moved.user == caretaker.user and PushSubscription.objects.count() == 1
    for bad in (None, {}, {"endpoint": ENDPOINT}, {**sub, "endpoint": "https://example.com/x"},
                {**sub, "keys": {"p256dh": "short", "auth": sub["keys"]["auth"]}},
                {**sub, "keys": {"p256dh": sub["keys"]["p256dh"], "auth": "!!"}},
                {**sub, "keys": {"p256dh": sub["keys"]["p256dh"], "auth": 5}}):
        with pytest.raises(ValidationError):
            push.subscribe(owner.user, bad)
    assert push.unsubscribe(owner.user) == 0  # not theirs any more
    assert push.unsubscribe(caretaker.user, ENDPOINT) == 1


def test_without_keys_alerts_are_not_offered(owner, device, client):
    with pytest.raises(ValidationError):
        push.subscribe(owner.user, device[1])
    login(client, owner)
    page = client.get(reverse("notifications:inbox")).content.decode()
    assert "data-push" not in page and "js/push.js" not in page


# ---------------------------------------------------------------------------
# Pushing alerts
# ---------------------------------------------------------------------------


def test_a_staff_alert_is_pushed_without_details(owner, keys, device, django_capture_on_commit_callbacks):
    push.subscribe(owner.user, device[1])
    with django_capture_on_commit_callbacks(execute=True):
        message = alert(owner)
    message.refresh_from_db()
    assert message.channel == catalog.IN_APP and not message.push_due
    [(endpoint, payload, urgent)] = MemoryPushSender.outbox
    assert endpoint == ENDPOINT and not urgent
    assert payload == {"title": "Payment waiting for review", "body": "Open NyumbaPMS to see it.",
                       "url": reverse("notifications:inbox"), "tag": str(message.public_id)}
    # No names, units or amounts reach the lock screen.
    assert not [w for w in ("Wanjiku", "A1", "5,000", "Otieno") if w in json.dumps(payload)]
    sub = PushSubscription.objects.get()
    assert sub.last_pushed_at is not None and sub.failures == 0
    # Pushed once only.
    assert push.push_message(message.pk) == 0 and len(MemoryPushSender.outbox) == 1


def test_who_and_when_is_pushed(owner, keys, device, django_capture_on_commit_callbacks):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    push.subscribe(caretaker.user, device[1])
    with django_capture_on_commit_callbacks(execute=True):
        nobody = alert(owner)  # the owner has no device
        quiet = alert(owner, caretaker.user, now=LATE)  # quiet hours: the bell only
        urgent = alert(owner, caretaker.user, now=LATE, urgent=True)
        tenant = tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")
        delivery.notify(owner.organization, "announcement", tenant=tenant, now=NOON, context={"text": "Hi"})
    assert not nobody.push_due and not quiet.push_due
    assert [(e, p["tag"], u) for e, p, u in MemoryPushSender.outbox] == [(ENDPOINT, str(urgent.public_id), False)]


def test_a_gone_or_failing_device_is_removed(owner, keys, device):
    push.subscribe(owner.user, device[1])
    MemoryPushSender.status = 500
    for n in range(1, PushSubscription.MAX_FAILURES):
        assert push.push_message(alert(owner, send_now=False).pk) == 0
        assert PushSubscription.objects.get().failures == n
    push.push_message(alert(owner, send_now=False).pk)
    assert not PushSubscription.objects.exists()
    push.subscribe(owner.user, device[1])
    MemoryPushSender.status = 410
    push.push_message(alert(owner, send_now=False).pk)
    assert not PushSubscription.objects.exists()


def test_bulk_alerts_are_pushed_by_the_job_and_old_ones_dropped(owner, keys, device):
    push.subscribe(owner.user, device[1])
    old = alert(owner, send_now=False)
    Message.objects.filter(pk=old.pk).update(created_at=timezone.now() - datetime.timedelta(hours=2))
    fresh = alert(owner, send_now=False)
    assert not MemoryPushSender.outbox
    out = StringIO()
    call_command("send_due_messages", stdout=out)
    assert "pushed: 1" in out.getvalue() and "push dropped: 1" in out.getvalue()
    assert [p["tag"] for _e, p, _u in MemoryPushSender.outbox] == [str(fresh.public_id)]
    assert not Message.objects.filter(push_due=True).exists()


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


def turn(client, body):
    return client.post(reverse("notifications:push"), json.dumps(body), content_type="application/json")


def test_turning_alerts_on_and_off_on_this_device(client, owner, keys, device):
    login(client, owner)
    page = client.get(reverse("notifications:inbox")).content.decode()
    assert 'data-state="off"' in page and keys[0] in page and "js/push.js" in page
    assert "Turn off everywhere" not in page
    response = turn(client, {"action": "on", "subscription": device[1]})
    assert response.status_code == 200 and response.json() == {"status": "on"}
    page = client.get(reverse("notifications:inbox")).content.decode()
    assert 'data-state="on"' in page and "Alerts are on for 1 device." in page
    assert turn(client, {"action": "off", "endpoint": ENDPOINT}).json() == {"status": "off"}
    assert not PushSubscription.objects.exists()
    # Checked.
    assert turn(client, {"action": "on", "subscription": {**device[1], "endpoint": "https://evil.test/x"}}
                ).status_code == 422
    assert turn(client, {"action": "dance"}).status_code == 400
    assert client.post(reverse("notifications:push"), "{nope", content_type="application/json").status_code == 400
    assert turn(client, {"action": "on", "pad": "x" * 5000}).status_code == 413
    assert not PushSubscription.objects.exists()


def test_logging_out_turns_this_device_off_and_everywhere_turns_all_off(client, owner, keys, device):
    login(client, owner)
    turn(client, {"action": "on", "subscription": device[1]})
    client.post(reverse("accounts:logout"))
    assert not PushSubscription.objects.exists()
    login(client, owner)
    turn(client, {"action": "on", "subscription": device[1]})
    other = {**device[1], "endpoint": "https://updates.push.services.mozilla.com/wpush/v2/other"}
    push.subscribe(owner.user, other)
    response = client.post(reverse("notifications:push"), {"action": "off_all"})
    assert response.status_code == 302 and not PushSubscription.objects.exists()


def test_only_members_can_turn_alerts_on(client, keys, device):
    response = turn(client, {"action": "on", "subscription": device[1]})
    assert response.status_code == 302 and not PushSubscription.objects.exists()


def test_the_service_worker_shows_alerts():
    worker = pwa.service_worker()
    assert 'addEventListener("push"' in worker and 'addEventListener("notificationclick"' in worker
    assert "target.origin !== self.location.origin" in worker
