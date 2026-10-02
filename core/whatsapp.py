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


class ConsoleWhatsAppSender(WhatsAppSender):
    name = "console"

    def send(self, to, template, language, params):
        logger.warning("WhatsApp to %s: %s/%s %s", to, template, language, params)
        print(f"[WhatsApp to {to}] {template}/{language} {params}")
        return SmsResult(ok=True, provider=self.name)


class MemoryWhatsAppSender(WhatsAppSender):
    """Keeps messages in a list so tests can read them."""

    name = "memory"
    outbox: list[tuple[str, str, str, list[str]]] = []

    def send(self, to, template, language, params):
        MemoryWhatsAppSender.outbox.append((to, template, language, list(params)))
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

    def __init__(self):
        self.phone_number_id = getattr(settings, "WA_PHONE_NUMBER_ID", "")
        self.token = getattr(settings, "WA_ACCESS_TOKEN", "")
        if not self.phone_number_id or not self.token:
            raise ImproperlyConfigured("Set WA_PHONE_NUMBER_ID and WA_ACCESS_TOKEN to use the WhatsApp Cloud API.")
        self.url = self.URL.format(version=getattr(settings, "WA_API_VERSION", "v21.0"),
                                   phone_number_id=self.phone_number_id)

    def _post(self, payload: dict) -> dict:
        request = urllib.request.Request(
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

    def send(self, to, template, language, params):
        try:
            data = self._post(self.payload(to, template, language, params))
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


def get_whatsapp_sender() -> WhatsAppSender:
    return import_string(settings.WHATSAPP_BACKEND)()
