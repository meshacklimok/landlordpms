"""Bounces and spam complaints (D-223): the webhooks, the suppression list, and every place that honours it."""

import base64
import hashlib
import hmac
import json
import time

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.otp import OTPError, issue_otp
from accounts.tests.factories import make_user
from notifications import emails
from notifications.models import EmailSuppression, Message
from notifications.providers.backend import SuppressingSMTPBackend
from notifications.suppression import is_suppressed, suppress

pytestmark = pytest.mark.django_db

SECRET_RAW = b"0123456789abcdef0123456789abcdef"
SECRET = "whsec_" + base64.b64encode(SECRET_RAW).decode()


@pytest.fixture(autouse=True)
def _tokens(settings):
    settings.MAIL_HOOK_TOKEN = "tok"
    settings.RESEND_WEBHOOK_SECRET = SECRET


def _signed(payload: dict, *, secret_raw=SECRET_RAW, stamp=None) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    stamp = str(stamp or int(time.time()))
    sig = base64.b64encode(hmac.new(secret_raw, f"msg_1.{stamp}.".encode() + body, hashlib.sha256).digest()).decode()
    return body, {"HTTP_SVIX_ID": "msg_1", "HTTP_SVIX_TIMESTAMP": stamp, "HTTP_SVIX_SIGNATURE": f"v1,{sig}"}


def _resend(client, payload, **kw):
    body, headers = _signed(payload, **kw)
    return client.post(reverse("hook_mail", args=["tok", "resend"]), body, content_type="application/json", **headers)


def test_resend_permanent_bounce_lists_the_address_and_unverifies_the_user(client):
    user = make_user(email="gone@example.com", email_verified_at=timezone.now())
    r = _resend(client, {"type": "email.bounced", "data": {"to": ["Gone@Example.com"],
                                                           "bounce": {"type": "Permanent", "message": "no mailbox"}}})
    assert r.status_code == 200
    row = EmailSuppression.objects.get(email="gone@example.com")
    assert row.reason == "HARD_BOUNCE" and row.provider == "Resend"
    user.refresh_from_db()
    assert user.email_verified_at is None


def test_resend_complaint_is_listed_and_transient_bounce_is_not(client):
    _resend(client, {"type": "email.complained", "data": {"to": ["spam@example.com"]}})
    _resend(client, {"type": "email.bounced", "data": {"to": ["full@example.com"], "bounce": {"type": "Transient"}}})
    assert list(EmailSuppression.objects.values_list("email", "reason")) == [("spam@example.com", "COMPLAINT")]


def test_resend_needs_a_good_signature_and_the_right_token(client):
    payload = {"type": "email.complained", "data": {"to": ["a@example.com"]}}
    assert _resend(client, payload, secret_raw=b"x" * 32).status_code == 403
    assert _resend(client, payload, stamp=int(time.time()) - 3600).status_code == 403
    body, headers = _signed(payload)
    assert client.post(reverse("hook_mail", args=["wrong", "resend"]), body, content_type="application/json",
                       **headers).status_code == 404
    assert not EmailSuppression.objects.exists()


def test_resend_is_refused_while_its_signing_secret_is_unset(client, settings):
    settings.RESEND_WEBHOOK_SECRET = ""
    assert _resend(client, {"type": "email.complained", "data": {"to": ["a@example.com"]}}).status_code == 403


def test_brevo_events(client):
    url = reverse("hook_mail", args=["tok", "brevo"])
    for event, email in [("hard_bounce", "a@example.com"), ("spam", "b@example.com"),
                         ("soft_bounce", "c@example.com"), ("delivered", "d@example.com")]:
        assert client.post(url, json.dumps({"event": event, "email": email}),
                           content_type="application/json").status_code == 200
    assert set(EmailSuppression.objects.values_list("email", flat=True)) == {"a@example.com", "b@example.com"}


def test_the_backend_drops_listed_recipients_and_skips_a_message_with_none_left(monkeypatch):
    from django.core.mail import EmailMessage
    from django.core.mail.backends.smtp import EmailBackend

    suppress("dead@example.com", "HARD_BOUNCE")
    sent = []
    monkeypatch.setattr(EmailBackend, "send_messages", lambda self, msgs: sent.extend(msgs) or len(msgs))
    backend = SuppressingSMTPBackend(host="localhost", port=25, username="", password="", use_tls=False, use_ssl=False,
                                      timeout=5, ssl_keyfile="", ssl_certfile="")
    backend.send_messages([EmailMessage("s", "b", "f@example.com", ["dead@example.com"]),
                           EmailMessage("s", "b", "f@example.com", ["Dead@example.com", "ok@example.com"])])
    assert [m.to for m in sent] == [["ok@example.com"]]


def test_a_sign_in_code_is_not_sent_to_a_listed_address():
    suppress("dead@example.com", "HARD_BOUNCE")
    with pytest.raises(OTPError):
        issue_otp("+254700000099", "VERIFY_EMAIL", email="dead@example.com")
    assert not mail.outbox


def test_tenant_mail_to_a_listed_address_fails_for_good():
    suppress("dead@example.com", "COMPLAINT")
    msg = Message(to="dead@example.com")
    result = emails.send(msg)
    assert not result.ok and result.permanent
    assert is_suppressed("DEAD@example.com")
