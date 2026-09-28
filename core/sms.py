"""SMS provider interface (doc 11 §11). Swapping providers touches only an adapter.

Console adapter for development, memory adapter for tests, Africa's Talking for real sends (Phase 5).
"""

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SmsResult:
    ok: bool
    provider: str
    provider_id: str = ""
    error: str = ""
    # What the provider charged, if it says.
    cost: Decimal | None = None
    # Retrying cannot help (invalid number, blacklisted, do-not-disturb): fail at once.
    permanent: bool = False


class SmsSender:
    name = "base"

    def send(self, to: str, body: str) -> SmsResult:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleSmsSender(SmsSender):
    name = "console"

    def send(self, to: str, body: str) -> SmsResult:
        logger.warning("SMS to %s: %s", to, body)
        print(f"[SMS to {to}] {body}")
        return SmsResult(ok=True, provider=self.name)


class MemorySmsSender(SmsSender):
    """Keeps messages in a list so tests can read OTP codes."""

    name = "memory"
    outbox: list[tuple[str, str]] = []

    def send(self, to: str, body: str) -> SmsResult:
        MemorySmsSender.outbox.append((to, body))
        return SmsResult(ok=True, provider=self.name)


class AfricasTalkingSmsSender(SmsSender):
    """Africa's Talking bulk SMS (form API, version1/messaging). Settings: AT_USERNAME, AT_API_KEY,
    optional AT_SENDER_ID. The username "sandbox" uses the sandbox. [VERIFY codes and fields in the sandbox.]
    """

    name = "africastalking"
    LIVE_URL = "https://api.africastalking.com/version1/messaging"
    SANDBOX_URL = "https://api.sandbox.africastalking.com/version1/messaging"
    TIMEOUT = 15
    # Recipient statusCode values: 100 processed, 101 sent, 102 queued are accepted.
    ACCEPTED = {100, 101, 102}
    # Invalid number, unsupported number type, blacklisted, do-not-disturb: retrying will not help.
    PERMANENT = {403, 404, 406, 409}

    def __init__(self):
        self.username = getattr(settings, "AT_USERNAME", "")
        self.api_key = getattr(settings, "AT_API_KEY", "")
        self.sender_id = getattr(settings, "AT_SENDER_ID", "")
        if not self.username or not self.api_key:
            raise ImproperlyConfigured("Set AT_USERNAME and AT_API_KEY to use Africa's Talking.")
        self.url = self.SANDBOX_URL if self.username == "sandbox" else self.LIVE_URL

    def _post(self, fields: dict) -> dict:
        request = urllib.request.Request(
            self.url, data=urllib.parse.urlencode(fields).encode(), method="POST",
            headers={"apiKey": self.api_key, "Accept": "application/json",
                     "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(request, timeout=self.TIMEOUT) as response:  # noqa: S310 - fixed https URL
            return json.loads(response.read().decode())

    def send(self, to: str, body: str) -> SmsResult:
        fields = {"username": self.username, "to": to, "message": body}
        if self.sender_id:
            fields["from"] = self.sender_id
        try:
            data = self._post(fields)
        except urllib.error.HTTPError as e:
            # 4xx means the request itself is wrong (bad key, bad sender ID): retries will not help.
            return SmsResult(ok=False, provider=self.name, error=f"HTTP {e.code}", permanent=400 <= e.code < 500)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            return SmsResult(ok=False, provider=self.name, error=str(e)[:300] or e.__class__.__name__)
        recipients = (data.get("SMSMessageData") or {}).get("Recipients") or []
        if not recipients:
            message = (data.get("SMSMessageData") or {}).get("Message") or "No recipients in the response"
            return SmsResult(ok=False, provider=self.name, error=str(message)[:300])
        r = recipients[0]
        code = int(r.get("statusCode") or 0)
        if code in self.ACCEPTED:
            return SmsResult(ok=True, provider=self.name, provider_id=str(r.get("messageId") or ""),
                             cost=parse_cost(r.get("cost")))
        return SmsResult(ok=False, provider=self.name, error=f"{code} {r.get('status', '')}".strip(),
                         permanent=code in self.PERMANENT)


def parse_cost(value) -> Decimal | None:
    """ "KES 0.8000" -> Decimal("0.8000"); anything else -> None."""
    match = re.search(r"(\d+(?:\.\d+)?)", str(value or ""))
    if not match:
        return None
    try:
        return Decimal(match.group(1))
    except InvalidOperation:
        return None


def get_sms_sender() -> SmsSender:
    path = getattr(settings, "SMS_BACKEND", "core.sms.ConsoleSmsSender")
    return import_string(path)()


def send_sms(to: str, body: str) -> SmsResult:
    return get_sms_sender().send(to, body)
