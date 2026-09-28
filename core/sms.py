"""SMS provider interface (doc 11 §11). Swapping providers touches only an adapter.

Phase 1 ships the console adapter for development and a memory adapter for tests.
The Africa's Talking adapter arrives in Phase 5.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
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


def get_sms_sender() -> SmsSender:
    path = getattr(settings, "SMS_BACKEND", "core.sms.ConsoleSmsSender")
    return import_string(path)()


def send_sms(to: str, body: str) -> SmsResult:
    return get_sms_sender().send(to, body)
