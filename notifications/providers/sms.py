"""SMS provider interface (doc 11 §11). Swapping providers touches only an adapter.

Console adapter for development, memory adapter for tests, Africa's Talking or Celcom Africa for real sends.
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


class CelcomSmsSender(SmsSender):
    """Celcom Africa bulk SMS (JSON API, /api/services/sendsms/). Settings: CELCOM_API_KEY, CELCOM_PARTNER_ID,
    optional CELCOM_SENDER_ID (their "shortcode"). [VERIFY endpoint, fields and codes with a live test.]
    """

    name = "celcom"
    URL = "https://isms.celcomafrica.com/api/services/sendsms/"
    TIMEOUT = 15
    ACCEPTED = {200}
    # Network not allowed, validation errors (bad number or message), do-not-disturb: retrying will not help.
    # Seen live: 1003 "Validation Errors" with an `errors` map, 1006 "Invalid credentials".
    PERMANENT = {1002, 1003, 1012}
    # Sender name not registered on that network.
    INVALID_SENDER = 1001

    def __init__(self):
        self.api_key = getattr(settings, "CELCOM_API_KEY", "")
        self.partner_id = getattr(settings, "CELCOM_PARTNER_ID", "")
        self.sender_id = getattr(settings, "CELCOM_SENDER_ID", "")
        if not self.api_key or not self.partner_id:
            raise ImproperlyConfigured("Set CELCOM_API_KEY and CELCOM_PARTNER_ID to use Celcom Africa.")
        if not self.sender_id:  # Celcom refuses a send without a sender name ("Shortcode must not be empty")
            raise ImproperlyConfigured("Set CELCOM_SENDER_ID: Celcom needs an approved sender name on every message.")

    def _post(self, fields: dict) -> dict:
        request = urllib.request.Request(  # noqa: S310 - a fixed https URL
            self.URL, data=json.dumps(fields).encode(), method="POST",
            headers={"Accept": "application/json", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.TIMEOUT) as response:  # noqa: S310 - fixed https URL
            return json.loads(response.read().decode())

    def send(self, to: str, body: str, sender_id: str = "") -> SmsResult:
        """Sends under `sender_id` if given, else the platform's. A refused organization name goes again under
        the platform's, as with Africa's Talking."""
        result = self._send(to, body, sender_id or self.sender_id)
        if sender_id and sender_id != self.sender_id and result.error.split(" ", 1)[0] == str(self.INVALID_SENDER):
            logger.warning("Sender name %s refused for %s; sending under the platform name", sender_id, to)
            result = self._send(to, body, self.sender_id)
        return result

    def _send(self, to: str, body: str, sender_id: str) -> SmsResult:
        fields = {"apikey": self.api_key, "partnerID": self.partner_id, "pass_type": "plain",
                  "mobile": to.lstrip("+"), "message": body}
        if sender_id:
            fields["shortcode"] = sender_id
        try:
            data = self._post(fields)
        except urllib.error.HTTPError as e:
            return SmsResult(ok=False, provider=self.name, error=f"HTTP {e.code}", permanent=400 <= e.code < 500)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            return SmsResult(ok=False, provider=self.name, error=str(e)[:300] or e.__class__.__name__)
        # Success arrives as {"responses": [{...}]}; errors arrive flat, as {"response-code": 1003, ...}.
        responses = data.get("responses") if isinstance(data, dict) else None
        r = responses[0] if isinstance(responses, list) and responses else data
        if not isinstance(r, dict) or not any(k in r for k in ("response-code", "respose-code")):
            return SmsResult(ok=False, provider=self.name, error="No response entry from Celcom")
        # Their field is spelled "respose-code" in some replies.
        raw = r.get("response-code", r.get("respose-code"))
        try:
            code = int(raw or 0)
        except (TypeError, ValueError):
            code = 0
        if code in self.ACCEPTED:
            return SmsResult(ok=True, provider=self.name, provider_id=str(r.get("messageid") or ""),
                             sender_id=sender_id)
        description = str(r.get("response-description") or r.get("respose-description") or "")
        problems = r.get("errors") if isinstance(r.get("errors"), dict) else {}
        details = "; ".join(str(v) for field in problems.values() for v in
                            (field.values() if isinstance(field, dict) else [field]))
        error = f"{code} {description} {details}".strip()[:300]
        # A refused sender name is a validation error naming the shortcode: a fault in our settings, not in this
        # message, so it is not permanent (and the caller may try the platform's name).
        if code == 1003 and "shortcode" in problems:
            code = self.INVALID_SENDER
            error = f"{code} {details or description}"[:300]
        return SmsResult(ok=False, provider=self.name, error=error, permanent=code in self.PERMANENT)

    DLR_URL = "https://isms.celcomafrica.com/api/services/getdlr/"
    # Their sample report: delivery-status 32, "DeliveredToTerminal". [VERIFY the other statuses live.]
    DELIVERED_WORDS = ("delivered",)
    FAILED_WORDS = ("undeliver", "reject", "expire", "fail", "absent", "blacklist", "invalid", "barred")

    def delivery_status(self, message_id: str) -> str:
        """Asks Celcom what became of a sent message: "delivered", "failed" or "pending". A reply we cannot read, or
        any error, is "pending": the next run asks again."""
        fields = {"apikey": self.api_key, "partnerID": self.partner_id, "messageID": message_id}
        try:
            request = urllib.request.Request(  # noqa: S310 - a fixed https URL
                self.DLR_URL, data=json.dumps(fields).encode(), method="POST",
                headers={"Accept": "application/json", "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=self.TIMEOUT) as response:  # noqa: S310 - fixed https URL
                data = json.loads(response.read().decode())
        except (urllib.error.URLError, TimeoutError, ValueError):
            return "pending"
        entry = data.get("responses") if isinstance(data, dict) else None
        entry = entry[0] if isinstance(entry, list) and entry else data
        if not isinstance(entry, dict):
            return "pending"
        text = str(entry.get("delivery-description") or "").lower()
        if str(entry.get("delivery-status") or "") == "32" or any(w in text for w in self.DELIVERED_WORDS):
            return "delivered"
        if any(w in text for w in self.FAILED_WORDS):
            return "failed"
        return "pending"


def platform_sender_name() -> str:
    """The name tenants see when an organization has none of its own, from whichever provider is active."""
    if getattr(settings, "SMS_BACKEND", "").endswith("CelcomSmsSender"):
        return getattr(settings, "CELCOM_SENDER_ID", "") or ""
    return getattr(settings, "AT_SENDER_ID", "") or ""


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
    if org is None or not org.sms_sender_name:
        return ""
    from subscriptions import services as billing  # a Platform Admin can close this per organization (D-200)

    return org.sms_sender_name if billing.own_sender_allowed(org) else ""


def send_sms(to: str, body: str, org=None) -> SmsResult:
    """`org`: send under that organization's approved sender name, if it has one. Leave it out for login codes,
    security alerts and anything else that speaks for NyumbaPMS."""
    return get_sms_sender().send(to, body, sender_id=sender_id_for(org))
