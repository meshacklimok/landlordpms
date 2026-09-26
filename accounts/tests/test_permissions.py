import pytest
from django.core.exceptions import PermissionDenied

from accounts import services
from accounts.capabilities import ALL_CODENAMES, ROLE_TEMPLATES
from accounts.models import Organization
from accounts.permissions import can, effective_capabilities, require, visible_properties

from .factories import add_member, fresh, make_org, make_property

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("template", [t for t in ROLE_TEMPLATES if not t.is_owner], ids=lambda t: t.key)
def test_template_capabilities_are_exactly_what_can_allows(template):
    org = make_org().organization
    m = add_member(org, template.key, all_properties=True)
    allowed = {c for c in ALL_CODENAMES if can(m, c)}
    assert allowed == set(template.capabilities)


def test_owner_can_everything():
    owner = make_org()
    assert effective_capabilities(owner) == services.role_codenames(owner.role)
    assert can(owner, "mpesa.settings")


def test_unknown_capability_raises():
    with pytest.raises(ValueError):
        can(make_org(), "nope.nothing")


def test_require_raises_permission_denied():
    org = make_org().organization
    with pytest.raises(PermissionDenied):
        require(add_member(org, "viewer"), "staff.manage")


def test_override_grant_and_withhold():
    owner = make_org()
    m = add_member(owner.organization, "caretaker")
    assert not can(m, "tenants.manage")
    services.set_override(owner, m, "tenants.manage", True)
    services.set_override(owner, m, "tenants.view", False)
    m = fresh(m)
    assert can(m, "tenants.manage")
    assert not can(m, "tenants.view")
    services.set_override(owner, m, "tenants.view", None)
    assert can(fresh(m), "tenants.view")


def test_org_wide_capability_needs_all_properties():
    owner = make_org()
    m = add_member(owner.organization, "manager", all_properties=False)
    assert not can(m, "staff.view")
    services.set_property_scope(owner, m, all_properties=True)
    assert can(fresh(m), "staff.view")


def test_property_scope():
    owner = make_org()
    org = owner.organization
    p1, p2 = make_property(org), make_property(org)
    m = add_member(org, "caretaker", properties=[p1])
    assert can(m, "properties.view", p1)
    assert not can(m, "properties.view", p2)
    assert list(visible_properties(m)) == [p1]
    assert set(visible_properties(owner)) == {p1, p2}


def test_role_edit_applies_on_next_request():
    owner = make_org()
    m = add_member(owner.organization, "caretaker")
    assert not can(m, "prospects.view")
    caps = services.role_codenames(m.role) | {"prospects.view"}
    services.update_role(owner, m.role, capabilities=caps)
    assert can(fresh(m), "prospects.view")


@pytest.mark.parametrize(
    "breaker",
    [
        lambda m: setattr(m, "is_active", False),
        lambda m: setattr(m.user, "is_active", False),
        lambda m: m.archive(by=None),
        lambda m: m.role.archive(by=None),
        lambda m: m.organization.archive(by=None),
        lambda m: setattr(m.organization, "status", Organization.Status.FROZEN),
    ],
    ids=["suspended", "user-inactive", "membership-archived", "role-archived", "org-archived", "org-frozen"],
)
def test_unusable_membership_can_nothing(breaker):
    owner = make_org()
    breaker(owner)
    assert not can(owner, "properties.view")
    assert not can(owner, "audit.view_own")


def test_read_only_org_keeps_only_read_only_safe_capabilities():
    owner = make_org()
    owner.organization.status = Organization.Status.READ_ONLY
    assert can(owner, "properties.view")
    assert can(owner, "reports.export")
    assert can(owner, "subscription.manage")  # must be able to pay to get out of read-only
    assert not can(owner, "properties.manage")
    assert not can(owner, "payments.record")
    assert not can(owner, "staff.manage")
