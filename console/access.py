"""Who may open the developer console, and which pages (D-135).

Figures are counts and totals for the platform; the console never shows a tenant's name, phone or amount.
"""

from django.utils.translation import gettext_lazy as _

CAPABILITIES = {
    "overview": _("Overview: users, organizations and growth"),
    "revenue": _("Revenue: subscriptions, plans and what we are paid"),
    "behaviour": _("Payment behaviour: how tenants pay across the platform"),
    "engagement": _("Usage: who is active, which features, app and devices"),
    "organizations": _("Organizations: each organization's health and detail"),
    "health": _("Platform health: failed messages, unmatched money, support and security"),
    "finance": _("Money: what the app costs you, and your net profit"),
    "growth": _("Growth: who signs up, where, how big they are, and who leaves"),
    "channels": _("Messages and AI: emails, SMS, WhatsApp and assistant use against limits"),
    "staff": _("Console team: add people and choose what they see"),
}

ROLE_PRESETS = {
    "OWNER": list(CAPABILITIES),
    "ANALYST": ["overview", "behaviour", "engagement", "organizations", "growth", "channels"],
    "FINANCE": ["overview", "revenue", "behaviour", "finance"],
    "SUPPORT": ["organizations", "health"],
    "ENGINEER": ["overview", "engagement", "health", "channels"],
}


def seat(user):
    """The user's active console seat, or None. A superuser is an owner without a row."""
    from .models import PlatformStaff

    if user is None or not user.is_authenticated or not user.is_active:
        return None
    if user.is_superuser:
        return PlatformStaff(user=user, role=PlatformStaff.Role.OWNER, capabilities=list(CAPABILITIES))
    row = PlatformStaff.objects.filter(user=user, is_active=True).first()
    return row


def allowed(user) -> list[str]:
    row = seat(user)
    return [c for c in row.capabilities if c in CAPABILITIES] if row else []


def can(user, capability: str) -> bool:
    return capability in allowed(user)
