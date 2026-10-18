"""SMS provider interface (doc 11 §11). Swapping providers touches only an adapter.

Console adapter for development, memory adapter for tests, Africa's Talking for real sends (Phase 5).
An organization with an approved sender name of its own sends under it (D-120); everyone else, and every
login or security message, sends under the platform's AT_SENDER_ID.
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
    # The sender name it went out under; blank is the provider's or platform's default.
    sender_id: str = ""


class SmsSender:
    name = "base"

    def send(self, to: str, body: str, sender_id: str = "") -> SmsResult:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleSmsSender(SmsSender):
    name = "console"

    def send(self, to: str, body: str, sender_id: str = "") -> SmsResult:
        logger.warning("SMS to %s%s: %s", to, f" from {sender_id}" if sender_id else "", body)
        print(f"[SMS to {to}{f' from {sender_id}' if sender_id else ''}] {body}")
        return SmsResult(ok=True, provider=self.name, sender_id=sender_id)


class MemorySmsSender(SmsSender):
    """Keeps messages in a list so tests can read OTP codes."""

    name = "memory"
    outbox: list[tuple[str, str]] = []
    # The sender name of each message in the outbox, in the same order.
    senders: list[str] = []

    def send(self, to: str, body: str, sender_id: str = "") -> SmsResult:
        MemorySmsSender.outbox.append((to, body))
        MemorySmsSender.senders.append(sender_id)
        return SmsResult(ok=True, provider=self.name, sender_id=sender_id)


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
    # InvalidSenderId: the name is not (or not yet) registered on that network. [VERIFY in the sandbox.]
    INVALID_SENDER = 402

    def __init__(self, username: str = "", api_key: str = "", sender_id: str = ""):
        """An organization's own account when given, else the platform's (AT_USERNAME, AT_API_KEY, AT_SENDER_ID)."""
        own = bool(username and api_key)
        self.username = username if own else getattr(settings, "AT_USERNAME", "")
        self.api_key = api_key if own else getattr(settings, "AT_API_KEY", "")
        self.sender_id = sender_id if own else getattr(settings, "AT_SENDER_ID", "")
        if not self.username or not self.api_key:
            raise ImproperlyConfigured("Set AT_USERNAME and AT_API_KEY to use Africa's Talking.")
        self.url = self.SANDBOX_URL if self.username == "sandbox" else self.LIVE_URL

    def _post(self, fields: dict) -> dict:
        request = urllib.request.Request(  # noqa: S310 - a fixed https URL
            self.url, data=urllib.parse.urlencode(fields).encode(), method="POST",
            headers={"apiKey": self.api_key, "Accept": "application/json",
                     "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(request, timeout=self.TIMEOUT) as response:  # noqa: S310 - fixed https URL
            return json.loads(response.read().decode())

    def send(self, to: str, body: str, sender_id: str = "") -> SmsResult:
        """Sends under `sender_id` if given, else the platform's. If a network refuses the organization's name
        (approved on Safaricom but not yet on Airtel, say), the message goes again under the platform's name."""
        result = self._send(to, body, sender_id or self.sender_id)
        if sender_id and sender_id != self.sender_id and result.error.split(" ", 1)[0] == str(self.INVALID_SENDER):
            logger.warning("Sender name %s refused for %s; sending under the platform name", sender_id, to)
            result = self._send(to, body, self.sender_id)
        return result

    def _send(self, to: str, body: str, sender_id: str) -> SmsResult:
        fields = {"username": self.username, "to": to, "message": body}
        if sender_id:
            fields["from"] = sender_id
        try:
            data = self._post(fields)
        except urllib.error.HTTPError as e:
            # 4xx means the request itself is wrong (bad key, bad sender ID): retries will not help.
            return SmsResult(ok=False, provider=self.name, error=f"HTTP {e.code}", permanent=400 <= e.code < 500)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            return SmsResult(ok=False, provider=self.name, error=str(e)[:300] or e.__class__.__name__)
        summary = data.get("SMSMessageData") if isinstance(data, dict) else None
        summary = summary if isinstance(summary, dict) else {}
        recipients = summary.get("Recipients") or []
        if not recipients or not isinstance(recipients[0], dict):
            message = str(summary.get("Message") or "No recipients in the response")
            if "invalidsenderid" in message.replace(" ", "").lower():
                message = f"{self.INVALID_SENDER} {message}"
            return SmsResult(ok=False, provider=self.name, error=message[:300])
        r = recipients[0]
        try:
            code = int(r.get("statusCode") or 0)
        except (TypeError, ValueError):
            code = 0
        if code in self.ACCEPTED:
            return SmsResult(ok=True, provider=self.name, provider_id=str(r.get("messageId") or ""),
                             cost=parse_cost(r.get("cost")), sender_id=sender_id)
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
    path = getattr(settings, "SMS_BACKEND", "notifications.providers.sms.ConsoleSmsSender")
    return import_string(path)()


def sender_id_for(org) -> str:
    """The organization's own approved sender name, or blank for the platform's (D-120)."""
    return org.sms_sender_name if org is not None else ""


def send_sms(to: str, body: str, org=None) -> SmsResult:
    """`org`: send under that organization's approved sender name, if it has one. Leave it out for login codes,
    security alerts and anything else that speaks for NyumbaPMS."""
    return get_sms_sender().send(to, body, sender_id=sender_id_for(org))
