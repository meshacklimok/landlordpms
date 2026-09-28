"""M-Pesa settings: encryption, accounts, Daraja keys, URL registration and the pages (D-045 step 1)."""

import io
import json
import urllib.error

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.urls import reverse

from accounts.tests.factories import add_member, make_org
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from core import crypto
from mpesa import models as mpesa_models
from mpesa import services
from mpesa.daraja import DarajaClient, DarajaError, FakeDarajaClient
from mpesa.models import DarajaCredentials
from payments.models import PaymentAccount

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def paybill(owner):
    return services.add_account(owner, type="PAYBILL", number="600123", display_name="Rent Paybill")


def connect(owner, account, **kw):
    kw.setdefault("environment", "SANDBOX")
    kw.setdefault("consumer_key", "key-1")
    kw.setdefault("consumer_secret", "secret-1")
    return services.save_credentials(owner, account, **kw)


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------


def test_encrypt_round_trip_and_empty(settings):
    settings.FIELD_ENCRYPTION_KEYS = Fernet.generate_key().decode()
    token = crypto.encrypt("s3cret")
    assert token != "s3cret" and crypto.decrypt(token) == "s3cret"
    assert crypto.encrypt("") == "" and crypto.decrypt("") == ""


def test_keys_rotate(settings):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    settings.FIELD_ENCRYPTION_KEYS = old
    token = crypto.encrypt("s3cret")
    settings.FIELD_ENCRYPTION_KEYS = f"{new},{old}"
    assert crypto.decrypt(token) == "s3cret"
    rotated = crypto.rotate(token)
    settings.FIELD_ENCRYPTION_KEYS = new
    assert crypto.decrypt(rotated) == "s3cret"
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(token)


def test_development_key_comes_from_the_secret_key(settings):
    settings.FIELD_ENCRYPTION_KEYS = ""
    assert crypto.decrypt(crypto.encrypt("x")) == "x"


def test_bad_key_is_a_configuration_error(settings):
    settings.FIELD_ENCRYPTION_KEYS = "not-a-key"
    with pytest.raises(ImproperlyConfigured):
        crypto.check_keys()


def test_callback_tokens_skip_words_daraja_refuses(monkeypatch):
    tokens = iter(["abcEXEdef", "xxMpEsAxx", "clean-token"])
    monkeypatch.setattr(mpesa_models.secrets, "token_urlsafe", lambda n: next(tokens))
    assert mpesa_models.new_callback_token() == "clean-token"


# ---------------------------------------------------------------------------
# Accounts and credentials
# ---------------------------------------------------------------------------


def test_add_account(owner, org):
    account = services.add_account(owner, type="TILL", number=" 543210 ", display_name="")
    assert (account.type, account.number, account.display_name) == ("TILL", "543210", "M-Pesa Till 543210")
    assert AuditEvent.objects.filter(action="payment_account.create").exists()
    with pytest.raises(ValidationError):
        services.add_account(owner, type="TILL", number="543210", display_name="")
    for bad in ({"type": "BANK", "number": "123456"}, {"type": "PAYBILL", "number": "12"},
                {"type": "PAYBILL", "number": "12a456"}):
        with pytest.raises(ValidationError):
            services.add_account(owner, display_name="", **bad)


def test_adding_needs_the_capability(org):
    accountant = add_member(org, "accountant", all_properties=True)
    with pytest.raises(PermissionDenied):
        services.add_account(accountant, type="PAYBILL", number="600123", display_name="")


def test_secrets_are_stored_encrypted(owner, paybill):
    creds = connect(owner, paybill, passkey="pass-1")
    creds.refresh_from_db()
    assert creds.shortcode == "600123" and creds.environment == "SANDBOX"
    assert "key-1" not in creds.consumer_key_encrypted and creds.secret("consumer_key") == "key-1"
    assert creds.secret("consumer_secret") == "secret-1" and creds.secret("passkey") == "pass-1"
    assert creds.can_connect and creds.can_request_payment
    event = AuditEvent.objects.get(action="mpesa.credentials")
    assert "key-1" not in json.dumps(event.changes) and "secret-1" not in json.dumps(event.changes)
    assert event.changes["changed"][1] == ["consumer_key", "consumer_secret", "environment", "passkey",
                                          "shortcode"]


def test_first_save_needs_both_keys(owner, paybill):
    with pytest.raises(ValidationError) as e:
        services.save_credentials(owner, paybill, environment="SANDBOX", consumer_key="k")
    assert set(e.value.message_dict) == {"consumer_secret"}


def test_blank_secrets_keep_the_stored_ones(owner, paybill):
    connect(owner, paybill)
    creds = services.save_credentials(owner, paybill, environment="SANDBOX", shortcode="", consumer_key="",
                                      consumer_secret="")
    assert creds.secret("consumer_key") == "key-1" and not creds.has("passkey")
    assert AuditEvent.objects.filter(action="mpesa.credentials").count() == 1


def test_changing_where_payments_go_needs_registering_again(owner, paybill):
    creds = connect(owner, paybill)
    services.register_urls(owner, creds)
    creds = services.save_credentials(owner, paybill, environment="SANDBOX", passkey="p")
    assert creds.urls_registered_at is not None
    creds = services.save_credentials(owner, paybill, environment="SANDBOX", shortcode="600999")
    assert creds.urls_registered_at is None and creds.shortcode == "600999"


def test_credentials_need_mpesa_settings_and_the_right_account(owner, org, paybill):
    manager = add_member(org, "manager", all_properties=True)
    with pytest.raises(PermissionDenied):
        connect(manager, paybill)
    other = make_org()
    with pytest.raises(PermissionDenied):
        connect(other, paybill)
    bank = PaymentAccount.objects.create(organization=org, type="BANK", number="1", display_name="Bank")
    with pytest.raises(ValidationError):
        connect(owner, bank)
    with pytest.raises(ValidationError):
        connect(owner, paybill, environment="LIVE")


def test_till_passkey_does_not_enable_payment_requests(owner):
    till = services.add_account(owner, type="TILL", number="543210", display_name="")
    creds = connect(owner, till, shortcode="765432", passkey="p")
    assert creds.shortcode == "765432" and not creds.can_request_payment


# ---------------------------------------------------------------------------
# Registering URLs
# ---------------------------------------------------------------------------


def test_register_urls(settings, owner, paybill):
    settings.SITE_URL = "https://pms.example.com"
    creds = connect(owner, paybill)
    services.register_urls(owner, creds)
    creds.refresh_from_db()
    assert creds.urls_registered_at and creds.registration_error == ""
    shortcode, path, body = FakeDarajaClient.calls[0]
    assert (shortcode, path) == ("600123", "/mpesa/c2b/v2/registerurl")
    assert body["ResponseType"] == "Completed"
    assert body["ConfirmationURL"] == f"https://pms.example.com/hooks/c2b/{creds.callback_token}/confirm/"
    assert body["ValidationURL"].endswith("/validate/")


def test_register_failure_is_kept_for_the_page(owner, paybill):
    creds = connect(owner, paybill)
    FakeDarajaClient.fail = "401 Invalid Access Token"
    with pytest.raises(ValidationError):
        services.register_urls(owner, creds)
    creds.refresh_from_db()
    assert creds.urls_registered_at is None and creds.registration_error == "401 Invalid Access Token"


def test_live_needs_https_and_no_refused_words(settings, owner, paybill):
    creds = connect(owner, paybill, environment="PRODUCTION")
    settings.SITE_URL = "http://pms.example.com"
    with pytest.raises(ValidationError, match="https"):
        services.register_urls(owner, creds)
    settings.SITE_URL = "https://mpesa-rent.example.com"
    with pytest.raises(ValidationError, match="mpesa"):
        services.register_urls(owner, creds)
    assert FakeDarajaClient.calls == []


def test_new_token_needs_registering_again(owner, paybill):
    creds = connect(owner, paybill)
    services.register_urls(owner, creds)
    old = creds.callback_token
    creds = services.new_token(owner, creds)
    assert creds.callback_token != old and creds.urls_registered_at is None


# ---------------------------------------------------------------------------
# Daraja client
# ---------------------------------------------------------------------------


class Reply:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_client_gets_a_token_once_and_registers(monkeypatch, owner, paybill):
    creds = connect(owner, paybill)
    requests = []

    def urlopen(request, timeout):
        requests.append(request)
        if "oauth" in request.full_url:
            return Reply({"access_token": "tok", "expires_in": "3599"})
        return Reply({"ResponseCode": "0", "ResponseDescription": "Success"})

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = DarajaClient(creds)
    client.register_urls("https://x/c", "https://x/v")
    client.register_urls("https://x/c", "https://x/v")
    assert [r.full_url for r in requests] == [
        "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials",
        "https://sandbox.safaricom.co.ke/mpesa/c2b/v2/registerurl",
        "https://sandbox.safaricom.co.ke/mpesa/c2b/v2/registerurl"]
    assert requests[0].get_header("Authorization").startswith("Basic ")
    assert requests[1].get_header("Authorization") == "Bearer tok"
    assert json.loads(requests[1].data)["ShortCode"] == "600123"


def test_client_errors(monkeypatch, owner, paybill):
    creds = connect(owner, paybill)

    def refused(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 400, "Bad", {}, io.BytesIO(json.dumps(
            {"errorCode": "400.003.01", "errorMessage": "Invalid Access Token"}).encode()))

    monkeypatch.setattr("urllib.request.urlopen", refused)
    with pytest.raises(DarajaError, match="400.003.01 Invalid Access Token"):
        DarajaClient(creds).token()

    def down(request, timeout):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr("urllib.request.urlopen", down)
    with pytest.raises(DarajaError, match="Could not reach"):
        DarajaClient(creds).token()


def test_client_refused_registration(monkeypatch, owner, paybill):
    creds = connect(owner, paybill)
    monkeypatch.setattr(DarajaClient, "token", lambda self: "tok")
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda request, timeout: Reply({"ResponseCode": "1", "ResponseDescription": "Duplicate"}))
    with pytest.raises(DarajaError, match="Duplicate"):
        DarajaClient(creds).register_urls("https://x/c", "https://x/v")


# ---------------------------------------------------------------------------
# Callback route
# ---------------------------------------------------------------------------


def test_validation_accepts_with_the_right_token(client, settings, owner, paybill):
    creds = connect(owner, paybill)
    url = reverse("hook_daraja", args=[creds.callback_token, "validate"])
    r = client.post(url, {"TransID": "X"}, content_type="application/json")
    assert r.status_code == 200 and r.json()["ResultCode"] == 0
    assert client.post(reverse("hook_daraja", args=["wrong", "validate"]), {},
                       content_type="application/json").status_code == 404
    assert client.get(url).status_code == 405
    assert client.post(url, b"nope", content_type="application/json").status_code == 400
    settings.MPESA_ALLOWED_IPS = ["196.201.214.200"]
    assert client.post(url, {}, content_type="application/json").status_code == 404
    assert client.post(url, {}, content_type="application/json",
                       REMOTE_ADDR="196.201.214.200").status_code == 200


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_settings_pages(client, owner, org):
    login(client, owner)
    r = client.get(reverse("mpesa:settings"))
    assert r.status_code == 200 and "Add a Paybill or Till" in r.content.decode()
    r = client.post(reverse("mpesa:settings"), {"type": "PAYBILL", "number": "12", "display_name": ""})
    assert r.status_code == 400
    r = client.post(reverse("mpesa:settings"), {"type": "PAYBILL", "number": "600123", "display_name": "Rent"})
    account = PaymentAccount.objects.get(organization=org, number="600123")
    url = reverse("mpesa:account", args=[account.public_id])
    assert r.status_code == 302 and r.url == url

    assert client.post(url, {"environment": "SANDBOX", "consumer_key": "k"}).status_code == 400
    r = client.post(url, {"environment": "SANDBOX", "consumer_key": "key-1", "consumer_secret": "secret-1"})
    assert r.status_code == 302
    page = client.get(url).content.decode()
    assert "key-1" not in page and "secret-1" not in page and "Saved — leave blank to keep" in page
    assert "Not registered yet" in page

    client.post(url, {"action": "register"})
    assert DarajaCredentials.objects.get(payment_account=account).urls_registered_at
    assert "Connected" in client.get(reverse("mpesa:settings")).content.decode()
    old = account.daraja.callback_token
    client.post(url, {"action": "new_token"})
    assert DarajaCredentials.objects.get(payment_account=account).callback_token != old


def test_register_error_is_shown(client, owner, paybill):
    connect(owner, paybill)
    login(client, owner)
    FakeDarajaClient.fail = "Bad credentials"
    url = reverse("mpesa:account", args=[paybill.public_id])
    r = client.post(url, {"action": "register"}, follow=True)
    assert "Bad credentials" in r.content.decode()


def test_pages_are_closed_to_others(client, org, paybill):
    accountant = add_member(org, "accountant", all_properties=True)
    login(client, accountant)
    assert client.get(reverse("mpesa:settings")).status_code == 403
    other = make_org()
    login(client, other)
    assert client.get(reverse("mpesa:account", args=[paybill.public_id])).status_code == 404
    assert client.post(reverse("mpesa:account", args=[paybill.public_id]), {"action": "register"}).status_code == 404
