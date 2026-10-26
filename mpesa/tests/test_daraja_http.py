# ruff: noqa: F811
"""DarajaClient against a local stand-in for Safaricom over real HTTP (no FakeDarajaClient): OAuth, the
STK password, field limits, error replies, then the full STK flow with Safaricom's callback shapes."""

import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError

from billing.invoicing import lease_balance
from mpesa import daraja, services, stk
from mpesa.models import DarajaCredentials, StkRequest
from mpesa.tests.test_stk import (  # noqa: F401 - fixtures
    _media,
    callback,
    creds,
    lease,
    org,
    owner,
    post_hook,
    prop,
    send,
)
from payments.models import Payment

pytestmark = pytest.mark.django_db

KEY, SECRET, PASSKEY = "k", "s", "pk"


class Safaricom(BaseHTTPRequestHandler):
    log: list = []
    replies: dict = {}  # path -> (status, body) for one-off overrides

    def _send(self, status, body):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        Safaricom.log.append(("GET", self.path, None, self.headers.get("Authorization")))
        want = "Basic " + base64.b64encode(f"{KEY}:{SECRET}".encode()).decode()
        if self.headers.get("Authorization") != want:
            return self._send(400, {"errorCode": "400.008.01", "errorMessage": "Invalid Authentication passed"})
        self._send(200, {"access_token": "tok-abc", "expires_in": "3599"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Safaricom.log.append(("POST", self.path, body, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != "Bearer tok-abc":
            return self._send(401, {"requestId": "x", "errorCode": "404.001.03", "errorMessage": "Invalid Access Token"})
        if self.path in Safaricom.replies:
            return self._send(*Safaricom.replies.pop(self.path))
        if self.path == "/mpesa/stkpush/v1/processrequest":
            raw = base64.b64decode(body["Password"]).decode()
            assert raw == f"{body['BusinessShortCode']}{PASSKEY}{body['Timestamp']}"
            assert re.fullmatch(r"\d{14}", body["Timestamp"])
            assert body["TransactionType"] == "CustomerPayBillOnline"
            assert isinstance(body["Amount"], int) and re.fullmatch(r"2547\d{8}", body["PhoneNumber"])
            assert len(body["AccountReference"]) <= 12 and len(body["TransactionDesc"]) <= 13
            assert body["CallBackURL"].startswith("http")
            return self._send(200, {"MerchantRequestID": "29115-34620561-1", "CheckoutRequestID": "ws_CO_191220191020363925",
                                    "ResponseCode": "0", "ResponseDescription": "Success. Request accepted for processing",
                                    "CustomerMessage": "Success. Request accepted for processing"})
        if self.path == "/mpesa/stkpushquery/v1/query":
            return self._send(200, {"ResponseCode": "0", "ResultCode": "1032", "ResultDesc": "Request cancelled by user",
                                    "CheckoutRequestID": body["CheckoutRequestID"]})
        if self.path == "/mpesa/c2b/v2/registerurl":
            return self._send(200, {"OriginatorCoversationID": "x", "ResponseCode": "0", "ResponseDescription": "Success"})
        self._send(404, {"errorCode": "404.001.01", "errorMessage": "Resource not found"})

    def log_message(self, *args):
        pass


@pytest.fixture
def safaricom(monkeypatch, settings):
    server = HTTPServer(("127.0.0.1", 0), Safaricom)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Safaricom.log, Safaricom.replies = [], {}
    cache.clear()
    settings.MPESA_CLIENT = "mpesa.daraja.DarajaClient"
    settings.SITE_URL = "https://app.nyumbapms.com"
    monkeypatch.setitem(daraja.BASE_URLS, DarajaCredentials.Environment.SANDBOX, f"http://127.0.0.1:{server.server_port}")
    yield Safaricom
    server.shutdown()


def test_token_is_fetched_once_and_cached(safaricom, creds):
    client = daraja.get_client(creds)
    assert client.token() == client.token() == "tok-abc"
    assert [m for m, *_ in safaricom.log] == ["GET"]


def test_wrong_keys_say_so(safaricom, creds, owner, settings):
    services.save_credentials(owner, creds.payment_account, environment="SANDBOX", consumer_key="bad", consumer_secret="bad")
    with pytest.raises(ValidationError):
        services.check_keys(owner, DarajaCredentials.objects.get(pk=creds.pk))


def test_register_urls_sends_both_urls(safaricom, creds, owner):
    services.register_urls(owner, creds)
    body = safaricom.log[-1][2]
    assert body["ShortCode"] == creds.shortcode and body["ResponseType"] == "Completed"
    assert body["ConfirmationURL"].startswith("https://app.nyumbapms.com/hooks/c2b/") and body["ConfirmationURL"].endswith("/confirm/")
    assert body["ValidationURL"].endswith("/validate/")
    creds.refresh_from_db()
    assert creds.urls_registered_at and not creds.registration_error


def test_register_urls_failure_is_kept(safaricom, creds, owner):
    safaricom.replies["/mpesa/c2b/v2/registerurl"] = (400, {"errorCode": "500.003.1001", "errorMessage": "URLs already registered"})
    with pytest.raises(ValidationError):
        services.register_urls(owner, creds)
    creds.refresh_from_db()
    assert "500.003.1001" in creds.registration_error and creds.urls_registered_at is None


def test_full_stk_flow_over_http(safaricom, client, owner, lease, creds):
    req = send(owner, lease)
    assert req.checkout_request_id == "ws_CO_191220191020363925" and req.status == StkRequest.Status.PENDING
    push = next(b for m, p, b, a in safaricom.log if p.endswith("processrequest"))
    assert push["Amount"] == 15000 and push["PartyA"] == push["PhoneNumber"] == "254712345602" and push["PartyB"] == "600123"
    assert post_hook(client, creds, callback(req)).json()["ResultCode"] == 0
    req.refresh_from_db()
    assert req.status == StkRequest.Status.PAID and lease_balance(lease) == 0
    assert Payment.objects.get().status == Payment.Status.CONFIRMED


def test_cancelled_by_tenant_then_status_query(safaricom, owner, lease, creds):
    req = send(owner, lease)
    stk.check_status(req, actor=owner)
    req.refresh_from_db()
    assert req.status == StkRequest.Status.FAILED and req.result_code == "1032"


def test_still_on_the_phone_is_not_a_failure(safaricom, owner, lease, creds):
    req = send(owner, lease)
    safaricom.replies["/mpesa/stkpushquery/v1/query"] = (500, {"requestId": "r", "errorCode": "500.001.1001",
                                                               "errorMessage": "The transaction is being processed"})
    with pytest.raises(ValidationError, match="not answered"):
        stk.check_status(req, actor=owner)
    req.refresh_from_db()
    assert req.status == StkRequest.Status.PENDING


def test_safaricom_refusal_is_kept_as_failed(safaricom, owner, lease, creds):
    safaricom.replies["/mpesa/stkpush/v1/processrequest"] = (
        400, {"requestId": "r", "errorCode": "400.002.02", "errorMessage": "Bad Request - Invalid PhoneNumber"})
    with pytest.raises(ValidationError, match="Invalid PhoneNumber"):
        send(owner, lease)
    req = StkRequest.objects.get()
    assert req.status == StkRequest.Status.FAILED and "400.002.02" in req.result_desc


def test_unreachable_safaricom_is_a_clear_error(safaricom, owner, lease, creds, monkeypatch):
    monkeypatch.setitem(daraja.BASE_URLS, DarajaCredentials.Environment.SANDBOX, "http://127.0.0.1:1")
    with pytest.raises(ValidationError, match="Could not reach Daraja"):
        send(owner, lease)


def test_html_error_page_is_not_a_crash(safaricom, owner, lease, creds):
    safaricom.replies["/mpesa/stkpush/v1/processrequest"] = (502, b"<html>Bad gateway</html>")
    with pytest.raises(ValidationError):
        send(owner, lease)


def test_json_list_reply_is_not_a_crash(safaricom, owner, lease, creds):
    safaricom.replies["/mpesa/stkpush/v1/processrequest"] = (200, ["unexpected"])
    with pytest.raises(ValidationError):
        send(owner, lease)
    safaricom.replies["/mpesa/stkpush/v1/processrequest"] = (500, ["unexpected"])
    with pytest.raises(ValidationError):
        send(owner, lease, amount=15001)


# ---------------------------------------------------------------------------
# Our own Paybill (subscription payments), same wire, platform keys
# ---------------------------------------------------------------------------


@pytest.fixture
def platform(safaricom, settings):
    settings.PLATFORM_DARAJA_ENVIRONMENT = "SANDBOX"
    settings.PLATFORM_DARAJA_SHORTCODE = "888555"
    settings.PLATFORM_DARAJA_CONSUMER_KEY, settings.PLATFORM_DARAJA_CONSUMER_SECRET = KEY, SECRET
    settings.PLATFORM_DARAJA_PASSKEY = PASSKEY
    settings.PLATFORM_CALLBACK_TOKEN = "t" * 40
    settings.MPESA_ALLOWED_IPS = []
    return safaricom


def test_platform_keys_register_and_prompt_over_http(platform, client, owner):
    from django.core.management import call_command

    from subscriptions import collect, services
    from subscriptions.models import Plan, PlatformCheckout, SubscriptionInvoice

    call_command("platform_payments", "--register")
    reg = next(b for m, p, b, a in platform.log if p.endswith("registerurl"))
    assert reg["ShortCode"] == "888555" and reg["ConfirmationURL"].endswith(f"/hooks/plat/{'t' * 40}/confirm/")

    invoice = services.choose_plan(owner, Plan.objects.get(key="starter"), "MONTHLY")
    checkout = collect.start_mpesa(owner, invoice, "0712000111")
    push = next(b for m, p, b, a in platform.log if p.endswith("processrequest"))
    assert push["BusinessShortCode"] == push["PartyB"] == "888555" and push["Amount"] == 799
    assert checkout.status == PlatformCheckout.Status.PENDING and checkout.reference == "ws_CO_191220191020363925"
    body = {"Body": {"stkCallback": {"MerchantRequestID": "m", "CheckoutRequestID": checkout.reference, "ResultCode": 0,
                                     "ResultDesc": "ok", "CallbackMetadata": {"Item": [
                                         {"Name": "Amount", "Value": 799}, {"Name": "MpesaReceiptNumber", "Value": "QSUB1"},
                                         {"Name": "TransactionDate", "Value": 20260310101500},
                                         {"Name": "PhoneNumber", "Value": 254712000111}]}}}}
    import json as _json
    r = client.post(f"/hooks/plat/{'t' * 40}/stk/", _json.dumps(body), content_type="application/json")
    assert r.json()["ResultCode"] == 0
    checkout.refresh_from_db()
    invoice = SubscriptionInvoice.objects.get(pk=invoice.pk)
    assert checkout.status == PlatformCheckout.Status.PAID and invoice.status == SubscriptionInvoice.Status.PAID


def test_rate_limited_query_says_to_wait(safaricom, owner, lease, creds):
    req = send(owner, lease)
    safaricom.replies["/mpesa/stkpushquery/v1/query"] = (429, b"")
    with pytest.raises(ValidationError, match="limiting requests"):
        stk.check_status(req, actor=owner)
    req.refresh_from_db()
    assert req.status == StkRequest.Status.PENDING
