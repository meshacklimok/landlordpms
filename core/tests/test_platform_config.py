"""Platform settings typed into Django admin (D-207)."""

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from core import platform_config as pc
from core.models import PlatformSetting

pytestmark = pytest.mark.django_db


def base_settings(settings):
    """The environment-level settings object, under the fixture's overrides."""
    holder = settings._wrapped
    while hasattr(holder, "default_settings"):
        holder = holder.default_settings
    return holder


@pytest.fixture(autouse=True)
def reading_on(monkeypatch):
    monkeypatch.setattr(pc, "ENABLED", True)
    pc.clear_cache()
    yield
    pc.clear_cache()


def test_a_saved_value_beats_the_environment_and_secrets_are_stored_encrypted(settings):
    pc.save({"WA_ACCESS_TOKEN": "EAAB-typed-in-admin"})
    assert settings.WA_ACCESS_TOKEN == "EAAB-typed-in-admin"
    assert "typed" not in PlatformSetting.objects.get(key="WA_ACCESS_TOKEN").value


def test_clearing_falls_back_and_bools_are_real_bools(settings):
    pc.save({"LEGAL_REVIEWED": "1", "HOSTING_LOCATION": "Frankfurt"})
    assert settings.LEGAL_REVIEWED is True and settings.HOSTING_LOCATION == "Frankfurt"
    pc.save({}, clear={"HOSTING_LOCATION"})
    assert settings.HOSTING_LOCATION != "Frankfurt"


def test_override_settings_still_wins(settings):
    pc.save({"HOSTING_LOCATION": "Saved"})
    with override_settings(HOSTING_LOCATION="Test"):
        assert settings.HOSTING_LOCATION == "Test"
    assert settings.HOSTING_LOCATION == "Saved"


def test_shortcode_follows_the_paybill_when_blank(settings, monkeypatch):
    monkeypatch.setattr(base_settings(settings), "PLATFORM_DARAJA_SHORTCODE", "")
    pc.save({"PLATFORM_PAYBILL": "123456"})
    assert settings.PLATFORM_DARAJA_SHORTCODE == "123456"


def test_off_by_default_under_pytest(monkeypatch):
    monkeypatch.setattr(pc, "ENABLED", None)
    pc.save({"SUPPORT_EMAIL": "saved@x.co.ke"})
    assert pc.override("SUPPORT_EMAIL") is None


def test_admin_page_saves_validates_and_hides_secrets(client, monkeypatch, settings):
    for key in ("AT_USERNAME", "AT_API_KEY", "AT_CALLBACK_TOKEN"):
        monkeypatch.setattr(base_settings(settings), key, "")
    from accounts import mfa

    monkeypatch.setattr(mfa, "is_enabled", lambda user: True)
    monkeypatch.setattr(mfa, "session_verified", lambda request: True)
    user = get_user_model().objects.create_superuser("+254700000001", password="pw-Strong-123!")
    client.force_login(user)
    url = reverse("admin:core_platformsetting_changelist")
    page = client.get(url)
    assert page.status_code == 200 and b"Africa" in page.content

    bad = client.post(url, {"SMS_BACKEND": pc.SMS_AT})  # provider chosen, keys missing
    assert bad.status_code == 200 and PlatformSetting.objects.count() == 0

    ok = client.post(url, {"SMS_BACKEND": pc.SMS_AT, "AT_USERNAME": "nyumba", "AT_API_KEY": "atsk_12345678",
                           "AT_CALLBACK_TOKEN": "x" * 40, "SUPPORT_EMAIL": "help@x.co.ke"})
    assert ok.status_code == 302
    assert PlatformSetting.objects.filter(key="AT_API_KEY").exists()
    again = client.get(url)
    assert b"atsk_12345678" not in again.content and b"ends 5678" in again.content


def test_only_the_owner_reaches_the_page(client, monkeypatch):
    from accounts import mfa

    monkeypatch.setattr(mfa, "is_enabled", lambda user: True)
    monkeypatch.setattr(mfa, "session_verified", lambda request: True)
    staff = get_user_model().objects.create_user("+254700000002", password="pw-Strong-123!", is_staff=True)
    client.force_login(staff)
    assert client.get(reverse("admin:core_platformsetting_changelist")).status_code in (302, 403)


def test_a_blank_scan_model_follows_the_assistant_model_saved_in_admin(settings, monkeypatch):
    base = base_settings(settings)
    monkeypatch.setattr(base, "ASSISTANT_MODEL", "env-model")
    monkeypatch.setattr(base, "SCAN_MODEL", "env-model")
    pc.save({"ASSISTANT_MODEL": "admin-model"})
    assert settings.SCAN_MODEL == "admin-model"
    pc.save({"SCAN_MODEL": "scan-only"})
    assert settings.SCAN_MODEL == "scan-only"


# ---- End to end with made-up credentials: type them in admin, then watch each part use them ----

FAKE = {
    "SMS_BACKEND": pc.SMS_AT, "AT_USERNAME": "fakeco", "AT_API_KEY": "atsk_fakefakefake", "AT_SENDER_ID": "FAKECO",
    "AT_CALLBACK_TOKEN": "a" * 40, "USSD_CODE": "*384*99#",
    "WHATSAPP_BACKEND": pc.WA_CLOUD, "WA_PHONE_NUMBER_ID": "1122334455", "WA_ACCESS_TOKEN": "EAAfaketoken",
    "WA_APP_SECRET": "wa-app-secret", "WA_VERIFY_TOKEN": "w" * 40, "WA_API_VERSION": "v22.0",
    "OPENAI_API_KEY": "sk-fake-openai", "ASSISTANT_MODEL": "fake-model",
    "PLATFORM_PAYBILL": "400200", "PLATFORM_DARAJA_ENVIRONMENT": "SANDBOX", "PLATFORM_DARAJA_CONSUMER_KEY": "ck-fake",
    "PLATFORM_DARAJA_CONSUMER_SECRET": "cs-fake", "PLATFORM_DARAJA_PASSKEY": "pk-fake",
    "PLATFORM_CALLBACK_TOKEN": "p" * 40, "PLATFORM_PAYSTACK_SECRET_KEY": "sk_test_fakefakefake",
    "SUPPORT_EMAIL": "help@fake.co.ke", "LEGAL_NAME": "Fake Company Ltd",
}


@pytest.fixture
def typed_in_admin(client, monkeypatch, settings):
    from accounts import mfa

    monkeypatch.setattr(mfa, "is_enabled", lambda user: True)
    monkeypatch.setattr(mfa, "session_verified", lambda request: True)
    holder = settings._wrapped
    while hasattr(holder, "default_settings"):  # the test suite pins these; a real server does not
        for key in ("SMS_BACKEND", "OPENAI_API_KEY"):
            vars(holder).pop(key, None)
        holder = holder.default_settings
    for key in ("AT_USERNAME", "AT_API_KEY", "AT_CALLBACK_TOKEN", "WA_PHONE_NUMBER_ID", "WA_ACCESS_TOKEN",
                "PLATFORM_DARAJA_CONSUMER_KEY", "PLATFORM_DARAJA_CONSUMER_SECRET", "PLATFORM_CALLBACK_TOKEN",
                "PLATFORM_PAYSTACK_SECRET_KEY", "PLATFORM_DARAJA_PASSKEY", "PLATFORM_DARAJA_SHORTCODE"):
        monkeypatch.setattr(base_settings(settings), key, "")  # as if the environment file were empty
    client.force_login(get_user_model().objects.create_superuser("+254700000009", password="pw-Strong-123!"))
    response = client.post(reverse("admin:core_platformsetting_changelist"), FAKE)
    assert response.status_code == 302, response.content.decode()[:2000]
    pc.clear_cache()
    return client


def test_sms_goes_out_with_the_typed_in_account(typed_in_admin, monkeypatch):
    import io
    import json
    import urllib.request

    from notifications.providers.sms import AfricasTalkingSmsSender, get_sms_sender

    seen = {}

    def fake_urlopen(request, timeout=0):
        seen["url"], seen["key"], seen["body"] = request.full_url, request.headers.get("Apikey"), request.data.decode()
        reply = {"SMSMessageData": {"Recipients": [{"statusCode": 101, "messageId": "m1", "cost": "KES 0.8000"}]}}
        return io.BytesIO(json.dumps(reply).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sender = get_sms_sender()
    assert isinstance(sender, AfricasTalkingSmsSender)
    result = sender.send("+254700000000", "Your code is 123456")
    assert result.ok and seen["key"] == "atsk_fakefakefake" and "username=fakeco" in seen["body"]
    assert "from=FAKECO" in seen["body"] and "sandbox" not in seen["url"]


def test_callbacks_accept_only_the_typed_in_tokens(typed_in_admin, settings):
    import hashlib
    import hmac

    client = typed_in_admin
    client.logout()
    assert client.post(f"/hooks/sms/africastalking/{'a' * 40}/delivery/", {"id": "x", "status": "Success"}).status_code == 200
    assert client.post(f"/hooks/sms/africastalking/{'b' * 40}/delivery/", {"id": "x"}).status_code == 404
    assert client.post(f"/hooks/plat/{'z' * 40}/stk/", "{}", content_type="application/json").status_code == 404
    assert client.post(f"/hooks/plat/{'p' * 40}/stk/", "{}", content_type="application/json").status_code != 404
    body = b'{"event": "ping"}'
    bad = client.post(f"/hooks/plat/{'p' * 40}/ps/", body, content_type="application/json",
                      headers={"x-paystack-signature": "nope"})
    assert bad.status_code == 401
    good_sig = hmac.new(b"sk_test_fakefakefake", body, hashlib.sha512).hexdigest()
    good = client.post(f"/hooks/plat/{'p' * 40}/ps/", body, content_type="application/json",
                       headers={"x-paystack-signature": good_sig})
    assert good.status_code == 200


def test_payments_see_the_typed_in_daraja_and_paystack(typed_in_admin):
    from subscriptions import collect

    assert collect.mpesa_configured() and collect.prompt_configured() and collect.paystack_configured()
    assert collect.platform_paybill() == "400200" if hasattr(collect, "platform_paybill") else True
    assert collect.callback_url("stk").endswith(f"/hooks/plat/{'p' * 40}/stk/")
    from django.conf import settings

    assert settings.PLATFORM_DARAJA_SHORTCODE == "400200"  # follows the Paybill


def test_openai_and_whatsapp_use_the_typed_in_values(typed_in_admin, monkeypatch):
    from ai_assistant import client as ai
    from notifications.providers.whatsapp import CloudApiWhatsAppSender, get_whatsapp_sender

    seen = {}

    def fake_post(url, body, headers, timeout=0):
        seen["auth"], seen["model"] = headers["authorization"], body["model"]
        return {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}], "usage": {}}

    monkeypatch.setattr(ai, "_post", fake_post)
    try:
        ai.OpenAIClient().create(system="s", tools=[], messages=[{"role": "user", "content": "hello"}], max_tokens=10)
    except Exception:  # noqa: BLE001, S110  (only the request matters here)
        pass
    assert seen == {"auth": "Bearer sk-fake-openai", "model": "fake-model"}
    wa = get_whatsapp_sender()
    assert isinstance(wa, CloudApiWhatsAppSender)
    assert wa.token == "EAAfaketoken" and "v22.0/1122334455" in wa.url


def test_contact_details_and_the_launch_check_see_them(typed_in_admin):
    from django.conf import settings

    from ops import launch

    assert settings.SUPPORT_EMAIL == "help@fake.co.ke" and settings.LEGAL_NAME == "Fake Company Ltd"
    ok, text = launch._providers()[0]
    assert ok and "fakeco" in text
