"""Platform settings typed into Django admin instead of the server's environment file (D-207).

`REGISTRY` lists the settings the owner may set in admin: provider keys (Africa's Talking, WhatsApp,
OpenAI, our Paybill and Paystack), push keys and the public contact and legal details. A value saved
there beats the environment; blank falls back to the environment, then to the built-in default. Secrets
are stored encrypted (core.crypto). Settings the server needs before the database is up (SECRET_KEY,
database, Redis, mail logins, FIELD_ENCRYPTION_KEYS) stay in the environment.

`install()` makes `settings.NAME` answer from here for the registered names, so no call site changes.
Django's `override_settings` still wins, and nothing is read from the database while pytest runs
unless a test turns `ENABLED` on. Other server processes pick up a change within `TTL` seconds.
"""

import os
import threading
import time
from dataclasses import dataclass, field

SMS_AT = "notifications.providers.sms.AfricasTalkingSmsSender"
SMS_CONSOLE = "notifications.providers.sms.ConsoleSmsSender"
WA_CLOUD = "notifications.providers.whatsapp.CloudApiWhatsAppSender"

TTL = 20
ENABLED: bool | None = None  # None: on, except while pytest runs


@dataclass(frozen=True)
class Item:
    key: str
    label: str
    group: str
    help: str = ""
    secret: bool = False
    kind: str = "text"  # text | bool | choice
    choices: tuple = field(default_factory=tuple)
    min_length: int = 0
    prefixes: tuple = field(default_factory=tuple)


SMS = "SMS (Africa's Talking)"
WHATSAPP = "WhatsApp (Meta Cloud API)"
AI = "Assistant and scans (OpenAI)"
PAY = "Subscription payments to us (Paybill, Paystack)"
PUSH = "Phone alerts"
CONTACT = "Contacts and legal details"

REGISTRY: tuple[Item, ...] = (
    Item("SMS_BACKEND", "SMS provider", SMS, "Choose Africa's Talking to send real SMS.", kind="choice",
         choices=((SMS_AT, "Africa's Talking (real SMS)"), (SMS_CONSOLE, "Console (prints, sends nothing)"))),
    Item("AT_USERNAME", "Africa's Talking username", SMS, "'sandbox' uses their test environment."),
    Item("AT_API_KEY", "Africa's Talking API key", SMS, secret=True),
    Item("AT_SENDER_ID", "Platform SMS sender name", SMS, "The name tenants see when a landlord has no approved name of their own."),
    Item("AT_CALLBACK_TOKEN", "Africa's Talking callback token", SMS,
         "Secret part of the delivery, inbound and USSD callback URLs. At least 32 random characters.",
         secret=True, min_length=32),
    Item("USSD_CODE", "Tenant USSD code", SMS, "Shown to tenants, e.g. *384*123#."),

    Item("WHATSAPP_BACKEND", "WhatsApp provider", WHATSAPP, "Leave on 'Off' until the Meta account is approved.",
         kind="choice", choices=(("", "Off"), (WA_CLOUD, "Meta Cloud API"))),
    Item("WA_PHONE_NUMBER_ID", "Phone number ID", WHATSAPP),
    Item("WA_ACCESS_TOKEN", "Access token", WHATSAPP, secret=True),
    Item("WA_APP_SECRET", "App secret", WHATSAPP, "Used to check that callbacks really come from Meta.", secret=True),
    Item("WA_VERIFY_TOKEN", "Callback verify token", WHATSAPP, "At least 32 random characters.", secret=True,
         min_length=32),
    Item("WA_API_VERSION", "Graph API version", WHATSAPP, "e.g. v21.0"),

    Item("OPENAI_API_KEY", "OpenAI API key", AI, secret=True),
    Item("ASSISTANT_MODEL", "Assistant model", AI),
    Item("SCAN_MODEL", "Scan (OCR) model", AI, "Blank uses the assistant model."),

    Item("PLATFORM_PAYBILL", "Our Paybill number", PAY),
    Item("PLATFORM_DARAJA_ENVIRONMENT", "Daraja environment", PAY, kind="choice",
         choices=(("SANDBOX", "Sandbox (test)"), ("PRODUCTION", "Production (real money)"))),
    Item("PLATFORM_DARAJA_SHORTCODE", "Daraja shortcode", PAY, "Blank uses the Paybill number."),
    Item("PLATFORM_DARAJA_CONSUMER_KEY", "Daraja consumer key", PAY, secret=True),
    Item("PLATFORM_DARAJA_CONSUMER_SECRET", "Daraja consumer secret", PAY, secret=True),
    Item("PLATFORM_DARAJA_PASSKEY", "Daraja passkey", PAY, secret=True),
    Item("PLATFORM_CALLBACK_TOKEN", "Payment callback token", PAY,
         "Secret part of the payment callback URL. At least 32 random characters.", secret=True, min_length=32),
    Item("PLATFORM_PAYSTACK_SECRET_KEY", "Paystack secret key", PAY, secret=True, prefixes=("sk_test_", "sk_live_")),
    Item("PLATFORM_KRA_PIN", "Our KRA PIN", PAY),
    Item("PLATFORM_VAT_REGISTERED", "VAT registered", PAY, kind="bool"),
    Item("PLATFORM_VAT_RATE", "VAT rate (%)", PAY),

    Item("VAPID_PUBLIC_KEY", "Push public key", PUSH, "Set both push keys or neither."),
    Item("VAPID_PRIVATE_KEY", "Push private key", PUSH, secret=True),
    Item("VAPID_SUBJECT", "Push contact", PUSH, "mailto: or https:// address."),

    Item("SUPPORT_EMAIL", "Support email", CONTACT),
    Item("HELLO_EMAIL", "Sales email", CONTACT),
    Item("SUPPORT_PHONE", "Support phone", CONTACT),
    Item("DATA_PROTECTION_EMAIL", "Data protection email", CONTACT),
    Item("OPS_ALERT_EMAILS", "Alert emails", CONTACT, "Comma-separated. Server and security alerts go here."),
    Item("OPS_ALERT_PHONES", "Alert phone numbers", CONTACT, "Comma-separated, 2547... Alerts are also texted here."),
    Item("MPESA_SETUP_VIDEO_URL", "M-Pesa setup video link", CONTACT),
    Item("HOSTING_LOCATION", "Hosting location", CONTACT),
    Item("LEGAL_NAME", "Legal name", CONTACT),
    Item("LEGAL_REGISTRATION", "Company registration number", CONTACT),
    Item("LEGAL_ADDRESS", "Registered address", CONTACT),
    Item("ODPC_REGISTRATION", "ODPC registration number", CONTACT),
    Item("LEGAL_REVIEWED", "Terms and Privacy reviewed by a lawyer", CONTACT, kind="bool"),
)

BY_KEY = {i.key: i for i in REGISTRY}
GROUPS = tuple(dict.fromkeys(i.group for i in REGISTRY))
_TRUE = ("1", "true", "yes", "on")

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "values": {}}


def clear_cache() -> None:
    with _lock:
        _cache["at"] = 0.0


def stored() -> dict[str, str]:
    """Every saved, decrypted value, read at most once per `TTL` seconds per process."""
    now = time.monotonic()
    with _lock:
        if _cache["at"] and now - _cache["at"] < TTL:
            return _cache["values"]
    values: dict[str, str] = {}
    try:
        from django.db import transaction

        from . import crypto
        from .models import PlatformSetting

        with transaction.atomic():
            for row in PlatformSetting.objects.all():
                if row.key not in BY_KEY:
                    continue
                try:
                    values[row.key] = crypto.decrypt(row.value) if BY_KEY[row.key].secret else row.value
                except crypto.DecryptionError:
                    continue  # key rotated without re-saving: behave as if blank
    except Exception:  # noqa: BLE001  (table missing during migrate, no database yet, ...)
        values = {}
    with _lock:
        _cache["at"] = now
        _cache["values"] = values
    return values


def coerce(key: str, raw: str):
    if BY_KEY[key].kind == "bool":
        return raw.strip().lower() in _TRUE
    return raw


def override(key: str):
    """The saved value for `key`, or None when nothing is saved (or reading is switched off)."""
    active = ENABLED if ENABLED is not None else not os.environ.get("PYTEST_CURRENT_TEST")
    if not active or key not in BY_KEY:
        return None
    raw = stored().get(key)
    return None if raw in (None, "") else coerce(key, raw)


def save(values: dict[str, str | None], *, clear: set[str] = frozenset(), actor=None) -> list[str]:
    """Stores the given values (None or blank keeps the current one), clears `clear`. Returns changed keys."""
    from . import crypto
    from .models import PlatformSetting

    current = dict(stored())
    changed = []
    for key in clear:
        if key in BY_KEY and PlatformSetting.objects.filter(key=key).delete()[0]:
            changed.append(key)
    for key, value in values.items():
        item = BY_KEY.get(key)
        if item is None or value is None or value == "" or key in clear or current.get(key) == value:
            continue
        PlatformSetting.objects.update_or_create(
            key=key, defaults={"value": crypto.encrypt(value) if item.secret else value, "updated_by": actor})
        changed.append(key)
    clear_cache()
    return changed


def validate(key: str, value: str) -> str | None:
    """An error message for a value that would break the platform, else None."""
    item = BY_KEY[key]
    if value and item.min_length and len(value) < item.min_length:
        return f"Must be at least {item.min_length} characters."
    if value and item.prefixes and not value.startswith(item.prefixes):
        return "Must start with " + " or ".join(item.prefixes) + "."
    if value and key == "VAPID_SUBJECT" and not value.startswith(("mailto:", "https://")):
        return "Must start with mailto: or https://."
    return None


def mask(value: str) -> str:
    return "set (ends " + value[-4:] + ")" if len(value) > 8 else "set"


def install() -> None:
    """Makes `settings.NAME` answer from the saved values for every registered name."""
    from django.conf import LazySettings, settings

    if getattr(LazySettings, "_platform_config", False):
        return
    original = LazySettings.__getattr__

    def __getattr__(self, name):
        if name not in BY_KEY:
            return original(self, name)
        holder = self._wrapped
        while hasattr(holder, "default_settings"):  # a test's override_settings wins
            if name in vars(holder):
                return getattr(holder, name)
            holder = holder.default_settings
        saved = override(name)
        if saved is not None:
            return saved
        base = getattr(self._wrapped, name)  # not cached, so a later save still shows
        if name == "PLATFORM_DARAJA_SHORTCODE":
            return base or self.PLATFORM_PAYBILL
        if name == "SCAN_MODEL" and base == self._wrapped.ASSISTANT_MODEL:
            return self.ASSISTANT_MODEL  # blank scan model follows the assistant model, including one saved in admin
        return base

    LazySettings.__getattr__ = __getattr__
    LazySettings._platform_config = True
    for item in REGISTRY:  # drop values Django already cached
        settings.__dict__.pop(item.key, None)
