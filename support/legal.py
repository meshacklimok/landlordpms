"""The Terms of Service and Privacy Policy (D-095): one version for both, and the details that depend on the company."""

from django.conf import settings

# A new date when either text changes in a way that matters; signed-in users are then asked to accept again.
VERSION = "2026-10-12"


def details() -> dict:
    return {
        "legal_version": VERSION,
        "legal_reviewed": bool(getattr(settings, "LEGAL_REVIEWED", False)),
        "legal_name": getattr(settings, "LEGAL_NAME", "") or "NyumbaPMS",
        "support_email": getattr(settings, "SUPPORT_EMAIL", ""),
        "dpo_email": getattr(settings, "DATA_PROTECTION_EMAIL", "") or getattr(settings, "SUPPORT_EMAIL", ""),
        "hosting": getattr(settings, "HOSTING_LOCATION", ""),
        "legal_registration": getattr(settings, "LEGAL_REGISTRATION", ""),
        "legal_address": getattr(settings, "LEGAL_ADDRESS", ""),
        "odpc_registration": getattr(settings, "ODPC_REGISTRATION", ""),
        "message_text_days": getattr(settings, "MESSAGE_TEXT_DAYS", 730),
    }


def needs_acceptance(user) -> bool:
    return bool(user.is_authenticated and user.terms_version != VERSION)
