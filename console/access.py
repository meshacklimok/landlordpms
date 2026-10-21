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
    "fix": _("Fix problems: send failed messages and webhooks again (needs Health to see them)"),
    "support": _("Support and feedback: read what landlords send us"),
    "support_solve": _("Support and feedback: reply, add notes and close requests (needs the page above)"),
    "tools": _("Tools: links to Sentry errors, PostHog and Google Analytics"),
    "staff": _("Console team: add people and choose what they see"),
    "articles": _("Articles: write, publish and edit the public articles"),
    "announce": _("Notices: write, edit and send notices to one owner, some or all"),
}

ROLE_PRESETS = {
    "OWNER": list(CAPABILITIES),
    "ANALYST": ["overview", "behaviour", "engagement", "organizations", "growth", "channels", "articles", "tools"],
    "FINANCE": ["overview", "revenue", "behaviour", "finance"],
    "SUPPORT": ["organizations", "health", "support", "support_solve"],
    "ENGINEER": ["overview", "engagement", "health", "channels", "fix", "tools"],
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
