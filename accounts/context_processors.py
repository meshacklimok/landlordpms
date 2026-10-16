def organization(request):
    from ai_assistant.client import enabled as assistant_set_up  # the assistant app imports accounts
    from support.legal import needs_acceptance

    from . import portfolio
    from .navigation import nav_groups

    set_up = assistant_set_up()
    from .vocabulary import RENTAL

    membership = getattr(request, "membership", None)
    org = getattr(request, "organization", None)
    user = getattr(request, "user", None)
    return {
        "vocab": org.vocab if org else RENTAL,
        "assistant_set_up": set_up,
        "membership": membership,
        # A callable, so only pages that show the top bar build the menu.
        "nav_groups": lambda: nav_groups(request, membership, set_up),
        "organization": getattr(request, "organization", None),
        # Callables, asked only by pages that use them (D-150).
        "many_properties": lambda: portfolio.many_properties(org),
        "agency_tools": lambda: portfolio.agency_tools(org),
        "user_memberships": getattr(request, "user_memberships", []),
        # Staff whose email is missing or unconfirmed are asked to add or confirm it.
        "email_notice": bool(membership and user and user.is_authenticated and not user.email_verified_at),
        # New Terms to accept (D-095 item 5).
        "terms_notice": needs_acceptance(user) if user else False,
        # A callable: only the Account menu asks, and only for signed-in people (D-135).
        "has_console": lambda: _has_console(user),
        "announcements": lambda: _announcements(user),
    }


def _announcements(user) -> list:
    """Notices from the platform owner (D-137), for signed-in people only."""
    if not (user and user.is_authenticated):
        return []
    from django.db.models import Q
    from django.utils import timezone

    from analytics.models import Announcement

    live = Announcement.objects.filter(Q(ends_at__isnull=True) | Q(ends_at__gt=timezone.now()), is_active=True)
    return list(live[:3])


def _has_console(user) -> bool:
    from analytics.access import allowed

    return bool(user and user.is_authenticated and allowed(user))
