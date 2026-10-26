def organization(request):
    from ai_assistant.client import enabled as assistant_set_up  # the assistant app imports accounts
    from support.legal import needs_acceptance

    from . import demo, portfolio
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
        # The floating NyumbaPMS AI button: where the Ask link is in the menu (accounts.navigation).
        "show_ask_button": lambda: _show_ask(membership, set_up) and not demo.is_demo(request),
        # A callable, so only pages that show the top bar build the menu.
        "nav_groups": lambda: nav_groups(request, membership, set_up),
        "organization": getattr(request, "organization", None),
        # The public demo's banner: who to look around as (D-210). A callable, so only the demo asks.
        "demo_people": lambda: demo.people(request),
        "in_demo": lambda: demo.is_demo(request),
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
        "announcements": lambda: _announcements(user, org),
        # Who the platform owner is looking in as; a callable so only the banner asks.
        "viewing_label": lambda: _viewing_label(request),
    }


def _viewing_label(request) -> str:
    from console import viewas

    return viewas.label(request)


def _show_ask(membership, set_up) -> bool:
    from .permissions import can

    return bool(set_up and membership and (membership.organization.assistant_enabled
                                           or can(membership, "organization.manage")))


def _announcements(user, org=None) -> list:
    """Notices from the platform owner (D-137), for signed-in people only. One sent to chosen organizations shows
    only to people working in one of them."""
    if not (user and user.is_authenticated):
        return []
    from django.db.models import Q
    from django.utils import timezone

    from console.models import PlatformAnnouncement

    now = timezone.now()
    live = PlatformAnnouncement.objects.filter(Q(ends_at__isnull=True) | Q(ends_at__gt=now),
                                               Q(starts_at__isnull=True) | Q(starts_at__lte=now), is_active=True)
    live = live.filter(Q(organizations__isnull=True) | Q(organizations=org)).distinct() if org else         live.filter(organizations__isnull=True)
    return list(live[:3])


def _has_console(user) -> bool:
    from console.access import allowed

    return bool(user and user.is_authenticated and allowed(user))
