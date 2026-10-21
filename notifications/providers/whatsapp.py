"""WhatsApp provider interface (D-044 item 16). Swapping providers touches only an adapter.

Messages we start must use a template Meta approved, so an adapter sends a template name, its
language and the parameter values in order, never free text. Console adapter for development,
memory adapter for tests, Meta's Cloud API for real sends.
"""

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from .sms import SmsResult

logger = logging.getLogger(__name__)


class WhatsAppSender:
    name = "base"

    def send(self, to: str, template: str, language: str, params: list[str]) -> SmsResult:  # pragma: no cover
        raise NotImplementedError

    def send_text(self, to: str, body: str) -> SmsResult:  # pragma: no cover
        """Free text, allowed only as a reply within 24 hours of the person's own message (D-093 item 6)."""
        raise NotImplementedError


class ConsoleWhatsAppSender(WhatsAppSender):
    name = "console"

    def send(self, to, template, language, params):
        logger.warning("WhatsApp to %s: %s/%s %s", to, template, language, params)
        print(f"[WhatsApp to {to}] {template}/{language} {params}")
        return SmsResult(ok=True, provider=self.name)

    def send_text(self, to, body):
        logger.warning("WhatsApp text to %s: %s", to, body)
        print(f"[WhatsApp text to {to}] {body}")
        return SmsResult(ok=True, provider=self.name)


class MemoryWhatsAppSender(WhatsAppSender):
    """Keeps messages in a list so tests can read them."""

    name = "memory"
    outbox: list[tuple[str, str, str, list[str]]] = []

    def send(self, to, template, language, params):
        MemoryWhatsAppSender.outbox.append((to, template, language, list(params)))
        return SmsResult(ok=True, provider=self.name, provider_id=f"wamid.test{len(MemoryWhatsAppSender.outbox)}")

    def send_text(self, to, body):
        MemoryWhatsAppSender.outbox.append((to, "text", "", [body]))
        return SmsResult(ok=True, provider=self.name, provider_id=f"wamid.test{len(MemoryWhatsAppSender.outbox)}")


class CloudApiWhatsAppSender(WhatsAppSender):
    """Meta WhatsApp Cloud API. Settings: WA_PHONE_NUMBER_ID, WA_ACCESS_TOKEN, WA_API_VERSION.
    [VERIFY the error codes against the Cloud API error reference.]
    """

    name = "whatsapp"
    URL = "https://graph.facebook.com/{version}/{phone_number_id}/messages"
    TIMEOUT = 15
    # Throttling: waiting and trying again can help.
    RETRY_CODES = {4, 80007, 130429, 131048, 131056}

    def __init__(self, phone_number_id: str = "", token: str = ""):
        """An organization's own number when given, else the platform's (WA_PHONE_NUMBER_ID, WA_ACCESS_TOKEN)."""
        self.phone_number_id = phone_number_id or getattr(settings, "WA_PHONE_NUMBER_ID", "")
        self.token = token or getattr(settings, "WA_ACCESS_TOKEN", "")
        if not self.phone_number_id or not self.token:
            raise ImproperlyConfigured("Set WA_PHONE_NUMBER_ID and WA_ACCESS_TOKEN to use the WhatsApp Cloud API.")
        self.version = getattr(settings, "WA_API_VERSION", "v21.0")
        self.url = self.URL.format(version=self.version, phone_number_id=self.phone_number_id)

    def describe(self) -> dict:
        """The number's details from Meta ({"display_phone_number", "verified_name"}). Raises ValueError if refused."""
        url = (f"https://graph.facebook.com/{self.version}/{self.phone_number_id}"
               "?fields=display_phone_number,verified_name")
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=self.TIMEOUT) as response:  # noqa: S310 - fixed https URL
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as e:
            raise ValueError(self._http_error(e).error) from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise ValueError(str(e)[:300] or e.__class__.__name__) from None

    def _post(self, payload: dict) -> dict:
        request = urllib.request.Request(  # noqa: S310 - a fixed https URL
            self.url, data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.TIMEOUT) as response:  # noqa: S310 - fixed https URL
            return json.loads(response.read().decode())

    @staticmethod
    def payload(to: str, template: str, language: str, params: list[str]) -> dict:
        body = {"name": template, "language": {"code": language}}
        if params:
            body["components"] = [{"type": "body",
                                   "parameters": [{"type": "text", "text": p} for p in params]}]
        return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to.lstrip("+"),
                "type": "template", "template": body}

    @staticmethod
    def text_payload(to: str, body: str) -> dict:
        return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to.lstrip("+"),
                "type": "text", "text": {"preview_url": False, "body": body[:4096]}}

    def send_text(self, to, body):
        return self._send(self.text_payload(to, body))

    def send(self, to, template, language, params):
        return self._send(self.payload(to, template, language, params))

    def _send(self, payload: dict) -> SmsResult:
        try:
            data = self._post(payload)
        except urllib.error.HTTPError as e:
            return self._http_error(e)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            return SmsResult(ok=False, provider=self.name, error=str(e)[:300] or e.__class__.__name__)
        messages = data.get("messages") or []
        if not messages or not messages[0].get("id"):
            return SmsResult(ok=False, provider=self.name, error="No message id in the response")
        return SmsResult(ok=True, provider=self.name, provider_id=str(messages[0]["id"]))

    def _http_error(self, e: urllib.error.HTTPError) -> SmsResult:
        try:
            error = json.loads(e.read().decode()).get("error") or {}
        except (ValueError, AttributeError, OSError):
            error = {}
        code = error.get("code")
        detail = (error.get("error_data") or {}).get("details") or error.get("message") or ""
        text = f"{code} {detail}".strip() if code else f"HTTP {e.code}"
        retry = e.code == 429 or e.code >= 500 or code in self.RETRY_CODES
        return SmsResult(ok=False, provider=self.name, error=text[:300], permanent=not retry)


def get_whatsapp_sender(organization=None) -> WhatsAppSender:
    """The organization's own number when it has set one up (Messages > Settings), else the platform's."""
    if organization is not None:
        from notifications.whatsapp_account import usable_account  # notifications builds on core

        account = usable_account(organization)
        if account is not None:
            return CloudApiWhatsAppSender(account.phone_number_id, account.secret("access_token"))
    from notifications.whatsapp_account import platform_number  # notifications builds on core

    shared = platform_number()
    if shared is not None:  # set by the platform owner in Django admin; replaces the WA_* settings
        return CloudApiWhatsAppSender(shared.phone_number_id, shared.secret("access_token"))
    return import_string(settings.WHATSAPP_BACKEND)()
