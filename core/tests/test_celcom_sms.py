"""The Celcom Africa adapter: request fields, response parsing, retryable vs permanent failures."""

import urllib.error

import pytest
from django.core.exceptions import ImproperlyConfigured

from notifications.providers.sms import CelcomSmsSender, platform_sender_name


@pytest.fixture
def celcom(settings):
    settings.CELCOM_API_KEY = "key"
    settings.CELCOM_PARTNER_ID = "123"
    settings.CELCOM_SENDER_ID = "NYUMBA"
    return CelcomSmsSender()


def reply(sender, monkeypatch, data=None, raises=None):
    sent = []

    def fake_post(fields):
        sent.append(fields)
        if raises:
            raise raises
        return data

    monkeypatch.setattr(sender, "_post", fake_post)
    return sent


def answer(code, description="Success", message_id=8290842, key="response-code"):
    return {"responses": [{key: code, "response-description": description, "mobile": 254712345678,
                           "messageid": message_id, "networkid": "1"}]}


def test_needs_key_and_partner_id(settings):
    settings.CELCOM_API_KEY, settings.CELCOM_PARTNER_ID = "", ""
    with pytest.raises(ImproperlyConfigured):
        CelcomSmsSender()


def test_accepted_message_keeps_id_and_strips_plus(celcom, monkeypatch):
    sent = reply(celcom, monkeypatch, answer(200))
    result = celcom.send("+254712345678", "Hello")
    assert result.ok and result.provider == "celcom" and result.provider_id == "8290842"
    assert result.sender_id == "NYUMBA"
    assert sent == [{"apikey": "key", "partnerID": "123", "pass_type": "plain", "mobile": "254712345678",
                     "message": "Hello", "shortcode": "NYUMBA"}]


def test_misspelled_code_field_is_read(celcom, monkeypatch):
    reply(celcom, monkeypatch, answer(200, key="respose-code"))
    assert celcom.send("254712345678", "Hello").ok


@pytest.mark.parametrize("code, permanent", [(1003, True), (1012, True), (1004, False), (1005, False)])
def test_rejected_message(celcom, monkeypatch, code, permanent):
    reply(celcom, monkeypatch, answer(code, "Rejected"))
    result = celcom.send("254712345678", "Hello")
    assert not result.ok and result.permanent is permanent and str(code) in result.error


def test_organization_name_refused_falls_back_to_platform_name(celcom, monkeypatch):
    sent = []

    def fake_post(fields):
        sent.append(fields.get("shortcode"))
        return answer(1001, "Invalid sender") if fields["shortcode"] == "KAMAU" else answer(200)

    monkeypatch.setattr(celcom, "_post", fake_post)
    result = celcom.send("254712345678", "Hello", sender_id="KAMAU")
    assert result.ok and sent == ["KAMAU", "NYUMBA"]


def test_empty_response_is_a_retryable_failure(celcom, monkeypatch):
    reply(celcom, monkeypatch, {"responses": []})
    result = celcom.send("254712345678", "Hello")
    assert not result.ok and not result.permanent


def test_http_4xx_is_permanent_and_network_errors_retry(celcom, monkeypatch):
    reply(celcom, monkeypatch, raises=urllib.error.HTTPError("u", 401, "no", {}, None))
    assert celcom.send("254712345678", "Hello").permanent
    reply(celcom, monkeypatch, raises=urllib.error.URLError("down"))
    result = celcom.send("254712345678", "Hello")
    assert not result.ok and not result.permanent


def test_platform_sender_name_follows_the_provider(settings):
    settings.AT_SENDER_ID, settings.CELCOM_SENDER_ID = "ATNAME", "CELNAME"
    settings.SMS_BACKEND = "notifications.providers.sms.AfricasTalkingSmsSender"
    assert platform_sender_name() == "ATNAME"
    settings.SMS_BACKEND = "notifications.providers.sms.CelcomSmsSender"
    assert platform_sender_name() == "CELNAME"
