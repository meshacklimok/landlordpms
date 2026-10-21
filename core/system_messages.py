"""The platform's own messages: sign-in and verification codes, security alerts, invitations, alerts to us.

Not the tenant notifications (those are in notifications.catalog). The owner can switch each kind on or off, and
each channel it can use, at any time in the console or Django admin (``SystemMessageSetting``). No row means on,
on every channel the kind can use.
"""
from dataclasses import dataclass

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

SMS = "SMS"
EMAIL = "EMAIL"
CHANNELS = (SMS, EMAIL)


@dataclass(frozen=True)
class Kind:
    key: str
    label: str
    help: str
    channels: tuple


KINDS = (
    Kind("code_verify_phone", _("Code to verify a phone number"), _("Sent when someone confirms their phone."), (SMS,)),
    Kind("code_verify_email", _("Code to verify an email address"), _("Sent when someone confirms their email."),
         (EMAIL,)),
    Kind("code_reset_password", _("Code to reset a password"),
         _("By email when the account has one, else by SMS. Switch one channel off to force the other."), (SMS, EMAIL)),
    Kind("password_changed", _("Password changed notice"), _("Tells the account holder their password changed."),
         (SMS, EMAIL)),
    Kind("security_alert_account", _("Security alert to the account holder"),
         _("Two-step login switched off or reset, or a recovery code used."), (SMS,)),
    Kind("security_alert_organization", _("Security alert to those who manage an organization"),
         _("Money or data could be sent elsewhere: payment settings, API keys, webhooks."), (SMS,)),
    Kind("invite_staff", _("Invitation to join an organization"), _("Link sent to a new team member."), (SMS,)),
    Kind("invite_tenant", _("Invitation to the tenant portal"), _("Link sent to a tenant."), (SMS,)),
    Kind("operator_alert", _("Problem and all-clear alerts to us"), _("Failed jobs and health checks."), (EMAIL,)),
    Kind("owner_digest", _("Daily summary to us"), _("Sign-ups, money in, support waiting and problems, each morning."),
         (EMAIL,)),
    Kind("support_request", _("New support request to us"), _("Copy of each support request."), (EMAIL,)),
    Kind("sender_name_request", _("SMS sender-name request to us"), _("When a landlord asks for their own name."),
         (EMAIL,)),
)
BY_KEY = {k.key: k for k in KINDS}

_CACHE_KEY = "core.system_message_settings"
_CACHE_SECONDS = 60


def _settings() -> dict:
    found = cache.get(_CACHE_KEY)
    if found is not None:
        return found
    from django.db import DatabaseError

    from .models import SystemMessageSetting

    try:
        found = {r.kind: r for r in SystemMessageSetting.objects.all()}
    except (DatabaseError, RuntimeError):
        return {}
    cache.set(_CACHE_KEY, found, _CACHE_SECONDS)
    return found


def clear_cache() -> None:
    cache.delete(_CACHE_KEY)


def channels(key: str) -> tuple:
    """The channels this kind may use right now. Empty when it is switched off."""
    kind = BY_KEY[key]
    row = _settings().get(key)
    if row is None:
        return tuple(kind.channels)
    if not row.enabled:
        return ()
    return tuple(c for c in kind.channels if (row.sms if c == SMS else row.email))


def allows(key: str, channel: str) -> bool:
    return channel in channels(key)
