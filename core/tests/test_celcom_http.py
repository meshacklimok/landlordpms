"""The Celcom adapter against a local stand-in server over real HTTP (no mocked _post): the request on the wire,
Celcom's documented reply shapes (including their "respose-code" misspelling), error codes and timeouts."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from notifications.providers.sms import CelcomSmsSender

CREDS = {"key": "k-123", "partner": "9999"}


class Handler(BaseHTTPRequestHandler):
    log: list = []
    script: list = []  # (http_status, body) popped per request

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        Handler.log.append({"path": self.path, "ctype": self.headers["Content-Type"], "body": json.loads(raw)})
        status, body = Handler.script.pop(0) if Handler.script else (200, ok())
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def ok(mobile=254712345678, mid=111):
    return {"responses": [{"respose-code": 200, "response-description": "Success", "mobile": mobile,
                           "messageid": mid, "network_id": "1"}]}


def fail(code, text):
    return {"responses": [{"respose-code": code, "response-description": text}]}


@pytest.fixture
def celcom(settings):
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Handler.log, Handler.script = [], []
    settings.CELCOM_API_KEY, settings.CELCOM_PARTNER_ID, settings.CELCOM_SENDER_ID = CREDS["key"], CREDS["partner"], "NYUMBAPMS"
    sender = CelcomSmsSender()
    sender.URL = f"http://127.0.0.1:{server.server_port}/api/services/sendsms/"
    sender.DLR_URL = sender.URL.replace("sendsms", "getdlr")
    yield sender
    server.shutdown()


def test_success_on_the_wire(celcom):
    result = celcom.send("+254712345678", "Rent due KES 15,000")
    assert result.ok and result.provider_id == "111" and result.sender_id == "NYUMBAPMS"
    sent = Handler.log[0]
    assert sent["path"] == "/api/services/sendsms/" and sent["ctype"] == "application/json"
    assert sent["body"] == {"apikey": "k-123", "partnerID": "9999", "pass_type": "plain",
                            "mobile": "254712345678", "message": "Rent due KES 15,000", "shortcode": "NYUMBAPMS"}


def test_unicode_and_long_body(celcom):
    assert celcom.send("254712345678", "Habari Wanjiku — kodi ya Oktoba ✓ " * 10).ok
    assert "✓" in Handler.log[0]["body"]["message"]


@pytest.mark.parametrize("code, text, permanent", [
    (1001, "Invalid sender id", False), (1002, "Network not allowed", True), (1003, "Invalid mobile number", True),
    (1004, "Low bulk credits", False), (1005, "Failed. System error", False), (1006, "Invalid credentials", False),
    (4090, "Internal Error. Try again", False), (4091, "No Partner ID is Set", False),
    (4092, "No API KEY Provided", False), (4093, "Details Not Found", False)])
def test_documented_error_codes(celcom, code, text, permanent):
    Handler.script = [(200, fail(code, text))] * 2
    result = celcom.send("254712345678", "Hi")
    assert not result.ok and result.permanent is permanent
    assert str(code) in result.error and text in result.error


def test_organization_name_refused_then_platform_name(celcom):
    Handler.script = [(200, fail(1001, "Invalid sender id")), (200, ok())]
    result = celcom.send("254712345678", "Hi", sender_id="KAMAU")
    assert result.ok and result.sender_id == "NYUMBAPMS"
    assert [r["body"]["shortcode"] for r in Handler.log] == ["KAMAU", "NYUMBAPMS"]


def test_http_errors_and_garbage(celcom):
    Handler.script = [(401, {"error": "no"})]
    assert celcom.send("254712345678", "Hi").permanent
    Handler.script = [(503, {"error": "down"})]
    r = celcom.send("254712345678", "Hi")
    assert not r.ok and r.error == "HTTP 503" and not r.permanent
    Handler.script = [(200, b"<html>gateway</html>")]
    r = celcom.send("254712345678", "Hi")
    assert not r.ok and not r.permanent
    Handler.script = [(200, b'["not","a","dict"]')]
    assert not celcom.send("254712345678", "Hi").ok
    Handler.script = [(200, {"responses": ["x"]})]
    assert not celcom.send("254712345678", "Hi").ok


def test_connection_refused_is_retryable(celcom):
    celcom.URL = "http://127.0.0.1:1/api/services/sendsms/"
    r = celcom.send("254712345678", "Hi")
    assert not r.ok and not r.permanent and r.error


# ---------------------------------------------------------------------------
# The whole path: queue -> send_one -> Celcom over HTTP -> Message row and wallet
# ---------------------------------------------------------------------------

import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from accounts.tests.factories import make_org  # noqa: E402
from notifications import delivery  # noqa: E402
from notifications.models import Message  # noqa: E402
from notifications.providers.sms import send_sms  # noqa: E402
from tenants import services as tenant_services  # noqa: E402

NOON = datetime.datetime(2026, 2, 3, 12, 0, tzinfo=ZoneInfo("Africa/Nairobi"))


@pytest.fixture
def live(celcom, settings, monkeypatch):
    settings.SMS_BACKEND = "notifications.providers.sms.CelcomSmsSender"
    monkeypatch.setattr(CelcomSmsSender, "URL", celcom.URL)
    monkeypatch.setattr(CelcomSmsSender, "DLR_URL", celcom.DLR_URL)
    return celcom


@pytest.fixture
def tenant_in_org(db):
    owner = make_org()
    return owner.organization, tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")


@pytest.mark.django_db
def test_queued_message_goes_out_through_celcom(live, tenant_in_org):
    org, tenant = tenant_in_org
    m = delivery.notify(org, "announcement", tenant=tenant, now=NOON, context={"text": "Water is off on Friday."})
    assert delivery.send_one(m.pk, NOON) == Message.Status.SENT
    m.refresh_from_db()
    assert (m.provider, m.provider_id, m.attempts) == ("celcom", "111", 1)
    body = Handler.log[0]["body"]
    assert body["mobile"] == "254712345678" and body["shortcode"] == "NYUMBAPMS"
    assert body["message"] == f"Water is off on Friday. {org.name}"


@pytest.mark.django_db
def test_invalid_number_fails_at_once_and_low_credit_retries(live, tenant_in_org):
    org, tenant = tenant_in_org
    m = delivery.notify(org, "announcement", tenant=tenant, now=NOON, context={"text": "Hi"})
    Handler.script = [(200, fail(1003, "Invalid mobile number"))]
    assert delivery.send_one(m.pk, NOON) == Message.Status.FAILED
    m2 = delivery.notify(org, "announcement", tenant=tenant, now=NOON, context={"text": "Hello"}, dedupe_key="b")
    Handler.script = [(200, fail(1004, "Low bulk credits"))]
    assert delivery.send_one(m2.pk, NOON) == Message.Status.QUEUED
    m2.refresh_from_db()
    assert "1004" in m2.error and m2.send_after > NOON
    assert delivery.send_one(m2.pk, m2.send_after) == Message.Status.SENT


@pytest.mark.django_db
def test_login_code_uses_the_platform_name(live):
    result = send_sms("+254712345678", "Your code is 123456")
    assert result.ok and Handler.log[0]["body"]["shortcode"] == "NYUMBAPMS"


@pytest.mark.django_db
def test_delivery_reports_are_pulled_and_applied(live, tenant_in_org):
    org, tenant = tenant_in_org
    ids = []
    for n in range(3):
        Handler.script = [(200, ok(mid=500 + n))]
        m = delivery.notify(org, "announcement", tenant=tenant, now=NOON, context={"text": f"Hi {n}"}, dedupe_key=f"d{n}")
        delivery.send_one(m.pk, NOON)
        ids.append(m.pk)
    dlr = lambda **kw: (200, {"response-code": 200, "response-description": "Success", **kw})  # noqa: E731
    Handler.script = [dlr(**{"message-id": 500, "delivery-status": 32, "delivery-description": "DeliveredToTerminal"}),
                      dlr(**{"message-id": 501, "delivery-status": 2, "delivery-description": "UndeliverableToTerminal"}),
                      (200, b"<html>busy</html>")]
    counts = delivery.check_celcom_reports(now=NOON)
    assert counts == {"celcom_delivered": 1, "celcom_failed": 1}
    got = {m.pk: m.status for m in Message.objects.filter(pk__in=ids).order_by("pk")}
    assert list(got.values()) == [Message.Status.DELIVERED, Message.Status.FAILED, Message.Status.SENT]
    sent = [r["body"] for r in Handler.log if r["path"].endswith("getdlr/")]
    assert [b["messageID"] for b in sent] == ["500", "501", "502"] and sent[0]["apikey"] == "k-123"


@pytest.mark.django_db
def test_reports_are_not_pulled_for_other_providers(settings):
    settings.SMS_BACKEND = "notifications.providers.sms.MemorySmsSender"
    assert delivery.check_celcom_reports() == {"celcom_delivered": 0, "celcom_failed": 0}


# Shapes seen from the real isms.celcomafrica.com with fake keys (2026-10-25).
LIVE_SENDER = {"response-code": 1003, "response-description": "Validation Errors. Check errors and try again",
               "errors": {"shortcode": {"Shortcode": "Sender ID is inactive or unassigned"}}}
LIVE_MESSAGE = {"response-code": 1003, "response-description": "Validation Errors. Check errors and try again",
                "errors": {"message": {"Message": "Message must not be empty"}}}


def test_live_flat_sender_refusal_falls_back_to_the_platform_name(celcom):
    Handler.script = [(200, LIVE_SENDER), (200, ok())]
    result = celcom.send("254712345678", "Hi", sender_id="KAMAU")
    assert result.ok and [r["body"]["shortcode"] for r in Handler.log] == ["KAMAU", "NYUMBAPMS"]


def test_live_platform_sender_refused_is_a_retryable_settings_fault(celcom):
    Handler.script = [(200, LIVE_SENDER)]
    result = celcom.send("254712345678", "Hi")
    assert not result.ok and not result.permanent and "inactive or unassigned" in result.error


def test_live_validation_error_is_permanent_and_says_why(celcom):
    Handler.script = [(200, LIVE_MESSAGE)]
    result = celcom.send("254712345678", "Hi")
    assert result.permanent and "Message must not be empty" in result.error


def test_live_invalid_credentials_flat(celcom):
    Handler.script = [(200, {"response-code": 1006, "response-description": "Invalid credentials"})]
    result = celcom.send("254712345678", "Hi")
    assert not result.ok and not result.permanent and result.error == "1006 Invalid credentials"


def test_sender_name_is_required(settings):
    settings.CELCOM_API_KEY, settings.CELCOM_PARTNER_ID, settings.CELCOM_SENDER_ID = "k", "1", ""
    from django.core.exceptions import ImproperlyConfigured
    with pytest.raises(ImproperlyConfigured):
        CelcomSmsSender()
