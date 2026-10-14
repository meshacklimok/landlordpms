"""Every route says who may use it (D-108 item 1).

test_hardening sweeps every route as a visitor and as another organization. This guard covers the gap inside one
organization: a staff page with no capability lets every role in, the caretaker included. A new route must
either name a real capability or be added to one of the lists below with the reason it is open.
"""

import inspect

import pytest
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

from accounts import mfa
from accounts.capabilities import CAPABILITY_MAP
from accounts.mixins import CapabilityRequiredMixin, OrgMemberRequiredMixin, VerifiedUserRequiredMixin
from accounts.models import Membership, Role
from accounts.tests.factories import make_org, make_user
from core.tests.test_hardening import PUBLIC
from core.tests.urlsweep import routes

# Staff pages open to every role, each checking inside what this member may do.
ANY_STAFF = {
    "accounts:home": "the home page shows only the tasks and figures the member's capabilities allow",
    "imports:list": "lists only the imports the member may make (imports.services.CAPABILITY per kind)",
    "imports:upload": "checks the capability for the kind of import",
    "imports:template": "checks the capability for the kind of import",
    "imports:template_xlsx": "checks the capability for the kind of import; lists only units the member can see",
    "imports:print": "checks the capability for the kind of import; lists only units the member can see",
    "imports:photo": "checks the capability for the kind of import",
    "imports:detail": "only the member's visible batches; sensitive columns need tenants.view_sensitive",
    "notifications:inbox": "the member's own inbox",
    "notifications:push": "the member's own phone alerts",
    "maintenance:list": "MaintenanceAccessMixin: maintenance.view or maintenance.view_assigned",
    "maintenance:detail": "MaintenanceAccessMixin",
    "maintenance:cost": "MaintenanceAccessMixin, and expenses capabilities inside",
    "maintenance:photo": "MaintenanceAccessMixin, and only photos of visible requests",
    "search:results": "each kind of record is searched only with its view capability",
    "search:suggest": "as search:results",
    "ai_assistant:ask": "every tool the assistant uses checks the member's capabilities",
    "ai_assistant:confirm": "each proposed action is checked again when confirmed",
}
# Pages for whoever is signed in, about themselves: their own account, or their tenancy in the portal.
SIGNED_IN = {
    "accounts:verify_phone", "accounts:verify_email", "accounts:email", "accounts:security", "accounts:onboarding", "accounts:switch_org",
    "support:accept_terms", "support:help", "support:help_topic", "support:contact",
    "portal:home", "portal:lease", "portal:repair_create", "portal:notice_create", "portal:repair",
    "portal:repair_photo", "portal:document", "portal:receipt", "portal:invoice_pdf", "portal:ask",
    "portal:ask_confirm", "portal:messages", "portal:lease_terms", "portal:offer_answer",
    "portal:payments", "portal:payment_report",  # own lease / own payment only; anything else is a 404
    "owner_portal:home", "owner_portal:account", "owner_portal:statement", "owner_portal:sent_pdf",
    "owner_portal:payments",  # the owner's own properties only; tenant names hidden
    # The developer console: 404 without a platform seat, MFA required, and each page checks its own capability.
    "console:overview", "console:revenue", "console:behaviour", "console:usage", "console:organizations",
    "console:organizations_export", "console:watchlist", "console:growth", "console:channels", "console:money", "console:costs", "console:expense", "console:organization_control", "console:announcements", "console:email", "console:audit", "console:organization", "console:health", "console:team", "console:team_add",
    "console:team_edit",
}
# Signed in by other means: an API key, or a password given but the two-step code not yet.
OWN_CHECK = {"api:me", "api:resource", "api:item", "accounts:login_mfa"}


def _kind(view) -> str:
    if not inspect.isclass(view):
        return "function"
    if issubclass(view, CapabilityRequiredMixin):
        return "capability" if view.required_capability else "staff, no capability"
    if issubclass(view, OrgMemberRequiredMixin):
        return "any staff"
    if issubclass(view, (VerifiedUserRequiredMixin, LoginRequiredMixin)):
        return "signed in"
    return "open"


def test_every_route_names_who_may_use_it():
    expected = {"any staff": set(ANY_STAFF), "signed in": SIGNED_IN, "open": PUBLIC | OWN_CHECK}
    problems = []
    for _path, name, view in routes():
        kind = _kind(view)
        if kind == "capability":
            if view.required_capability not in CAPABILITY_MAP:
                problems.append(f"{name}: unknown capability {view.required_capability!r}")
        elif name not in expected.get(kind, ()):
            problems.append(f"{name} ({view.__module__}.{view.__name__}) is {kind}: name a capability, "
                            "or add it to a list in this test with the reason")
    assert problems == [], "\n".join(problems)


def test_the_lists_have_no_stale_names():
    names = {name for _path, name, _view in routes()}
    assert (set(ANY_STAFF) | SIGNED_IN | OWN_CHECK | PUBLIC) - names == set()


@pytest.mark.django_db
def test_a_member_whose_role_has_no_capabilities_is_refused_everywhere_staff_only(rf):
    """Run against the views directly, so a mistake in a capability check is caught without seeded data."""
    owner = make_org()
    nobody = Role.objects.create(organization=owner.organization, name="Nobody")
    member = Membership.objects.create(user=make_user(), organization=owner.organization, role=nobody,
                                       all_properties=True)
    let_in = []
    for path, name, view in routes():
        if _kind(view) != "capability":
            continue
        for method in ("get", "post"):
            request = getattr(rf, method)(path)
            request.user, request.membership, request.organization = member.user, member, member.organization
            request.session = {mfa.VERIFIED: True}
            try:
                view.as_view()(request)
            except PermissionDenied:
                continue
            except Exception:  # noqa: BLE001, S110 - anything else got past the capability check
                pass
            let_in.append(f"{method} {name}")
    assert let_in == []
