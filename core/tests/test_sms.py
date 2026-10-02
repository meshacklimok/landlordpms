"""The Africa's Talking adapter: request fields, response parsing, retryable vs permanent failures."""

import io
import urllib.error
from decimal import Decimal

import pytest
from django.core.exceptions import ImproperlyConfigured

from core.sms import AfricasTalkingSmsSender, parse_cost


@pytest.fixture
def at(settings):
    settings.AT_USERNAME = "sandbox"
    settings.AT_API_KEY = "key"
    settings.AT_SENDER_ID = ""
    return AfricasTalkingSmsSender()


def reply(sender, monkeypatch, data=None, raises=None):
    sent = []

    def fake_post(fields):
        sent.append(fields)
        if raises:
            raise raises
        return data

    monkeypatch.setattr(sender, "_post", fake_post)
    return sent


def recipient(code, status="Success", message_id="ATXid_1", cost="KES 0.8000"):
    return {"SMSMessageData": {"Message": "Sent to 1/1", "Recipients": [
        {"statusCode": code, "status": status, "messageId": message_id, "cost": cost, "number": "+254712345678"}]}}


def test_needs_username_and_key(settings):
    settings.AT_USERNAME, settings.AT_API_KEY = "", ""
    with pytest.raises(ImproperlyConfigured):
        AfricasTalkingSmsSender()


def test_sandbox_username_uses_sandbox_url(settings, at):
    assert at.url == AfricasTalkingSmsSender.SANDBOX_URL
    settings.AT_USERNAME = "landlordpms"
    assert AfricasTalkingSmsSender().url == AfricasTalkingSmsSender.LIVE_URL


def test_accepted_message_keeps_id_and_cost(at, monkeypatch):
    at.sender_id = "LANDLORD"
    sent = reply(at, monkeypatch, recipient(101))
    result = at.send("+254712345678", "Hello")
    assert result.ok and result.provider == "africastalking"
    assert result.provider_id == "ATXid_1" and result.cost == Decimal("0.8000")
    assert sent == [{"username": "sandbox", "to": "+254712345678", "message": "Hello", "from": "LANDLORD"}]


@pytest.mark.parametrize("code, permanent", [(403, True), (406, True), (405, False), (500, False)])
def test_rejected_recipient(at, monkeypatch, code, permanent):
    reply(at, monkeypatch, recipient(code, status="InvalidPhoneNumber"))
    result = at.send("+254712345678", "Hello")
    assert not result.ok and result.permanent is permanent
    assert str(code) in result.error


def test_empty_recipients_is_a_retryable_failure(at, monkeypatch):
    reply(at, monkeypatch, {"SMSMessageData": {"Message": "InvalidSenderId", "Recipients": []}})
    result = at.send("+254712345678", "Hello")
    assert not result.ok and not result.permanent and result.error == "InvalidSenderId"


def test_http_4xx_is_permanent_5xx_and_network_are_not(at, monkeypatch):
    def http(code):
        return urllib.error.HTTPError(at.url, code, "x", {}, io.BytesIO(b""))

    reply(at, monkeypatch, raises=http(401))
    assert at.send("+254712345678", "x").permanent
    reply(at, monkeypatch, raises=http(503))
    assert not at.send("+254712345678", "x").permanent
    reply(at, monkeypatch, raises=urllib.error.URLError("down"))
    result = at.send("+254712345678", "x")
    assert not result.ok and not result.permanent
    reply(at, monkeypatch, raises=TimeoutError())
    assert at.send("+254712345678", "x").error == "TimeoutError"


@pytest.mark.parametrize("value, expected", [
    ("KES 0.8000", Decimal("0.8000")), ("0", Decimal("0")), ("KES 12", Decimal("12")), ("", None), (None, None),
])
def test_parse_cost(value, expected):
    assert parse_cost(value) == expected
