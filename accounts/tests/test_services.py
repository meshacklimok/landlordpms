"""Doc 13 safety rules: no escalation, Owner protection, last Owner, scope limits, invitations."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts import services
from accounts.capabilities import OWNER_CRITICAL
from accounts.models import Membership, Role
from audit.models import AuditEvent

from .factories import add_member, fresh, make_org, make_property, make_user, role

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def staff_admin(owner):
    """A Manager who was also given staff.manage and roles.manage: powerful, but not an Owner."""
    m = add_member(owner.organization, "manager", all_properties=True)
    services.set_override(owner, m, "staff.manage", True)
    services.set_override(owner, m, "roles.manage", True)
    return fresh(m)


# --- organization -----------------------------------------------------------------------------


def test_create_organization_makes_owner_with_all_properties(owner):
    assert owner.role.is_owner_role
    assert owner.all_properties
    assert AuditEvent.objects.filter(organization=owner.organization, action="organization.create").exists()


def test_create_organization_needs_verified_phone():
    with pytest.raises(PermissionDenied):
        services.create_organization(user=make_user(verified=False), name="X")


# --- no escalation ----------------------------------------------------------------------------


def test_cannot_create_role_with_capability_you_lack(staff_admin):
    with pytest.raises(PermissionDenied):
        services.create_role(staff_admin, name="Sneaky", capabilities=["mpesa.settings"])


def test_can_create_role_within_own_capabilities(staff_admin):
    r = services.create_role(staff_admin, name="Helper", capabilities=["properties.view", "tenants.view"])
    assert services.role_codenames(r) == {"properties.view", "tenants.view"}


def test_duplicate_role_name_is_rejected_case_insensitively(owner):
    with pytest.raises(ValidationError):
        services.create_role(owner, name="manager")


def test_cannot_add_capability_you_lack_to_existing_role(staff_admin):
    caretaker = role(staff_admin.organization, "caretaker")
    with pytest.raises(PermissionDenied):
        services.update_role(staff_admin, caretaker,
                             capabilities=services.role_codenames(caretaker) | {"payments.reverse"})


def test_cannot_grant_override_you_lack(staff_admin):
    m = add_member(staff_admin.organization, "caretaker")
    with pytest.raises(PermissionDenied):
        services.set_override(staff_admin, m, "mpesa.settings", True)


def test_can_withhold_capability_you_lack(staff_admin):
    m = add_member(staff_admin.organization, "caretaker")
    services.set_override(staff_admin, m, "maintenance.create", False)  # removing power is fine


def test_cannot_assign_role_stronger_than_yourself(staff_admin):
    m = add_member(staff_admin.organization, "caretaker")
    accountant = role(staff_admin.organization, "accountant")  # has expenses.approve, Manager doesn't
    with pytest.raises(PermissionDenied):
        services.change_member_role(staff_admin, m, accountant)


def test_cannot_invite_into_role_stronger_than_yourself(staff_admin):
    with pytest.raises(PermissionDenied):
        services.invite_staff(staff_admin, phone="0799000111", role=role(staff_admin.organization, "accountant"))


# --- Owner protection -------------------------------------------------------------------------


def test_non_owner_cannot_touch_owner_role(staff_admin):
    owner_role = Role.objects.get(organization=staff_admin.organization, is_owner_role=True)
    with pytest.raises(PermissionDenied):
        services.update_role(staff_admin, owner_role, name="Boss")


def test_non_owner_cannot_change_an_owner(owner, staff_admin):
    with pytest.raises(PermissionDenied):
        services.set_member_active(staff_admin, owner, False)
    with pytest.raises(PermissionDenied):
        services.set_override(staff_admin, owner, "tenants.view", False)


def test_non_owner_cannot_make_someone_owner(staff_admin):
    m = add_member(staff_admin.organization, "caretaker")
    owner_role = Role.objects.get(organization=staff_admin.organization, is_owner_role=True)
    with pytest.raises(PermissionDenied):
        services.change_member_role(staff_admin, m, owner_role)


@pytest.mark.parametrize("codename", sorted(OWNER_CRITICAL))
def test_owner_role_keeps_critical_capabilities(owner, codename):
    with pytest.raises(ValidationError):
        services.update_role(owner, owner.role, capabilities=services.role_codenames(owner.role) - {codename})


@pytest.mark.parametrize("codename", sorted(OWNER_CRITICAL))
def test_owner_cannot_be_denied_critical_capability(owner, codename):
    second = add_member(owner.organization, "owner", all_properties=True)
    with pytest.raises(ValidationError):
        services.set_override(owner, second, codename, False)


def test_owner_role_cannot_be_archived(owner):
    with pytest.raises(ValidationError):
        services.archive_role(owner, owner.role)


def test_owner_must_keep_all_properties(owner):
    second = add_member(owner.organization, "owner", all_properties=True)
    with pytest.raises(ValidationError):
        services.set_property_scope(owner, second, all_properties=False)


# --- last Owner -------------------------------------------------------------------------------


def test_last_owner_cannot_be_suspended_removed_or_demoted(owner):
    with pytest.raises(ValidationError):
        services.set_member_active(owner, owner, False)
    with pytest.raises(ValidationError):
        services.remove_member(owner, owner)
    with pytest.raises(ValidationError):
        services.change_member_role(owner, owner, role(owner.organization, "manager"))


def test_one_of_two_owners_can_leave(owner):
    second = add_member(owner.organization, "owner", all_properties=True)
    services.remove_member(owner, second)
    assert not Membership.objects.filter(pk=second.pk).exists()
    assert Membership.all_objects.get(pk=second.pk).is_archived
    with pytest.raises(ValidationError):
        services.remove_member(owner, owner)


def test_inactive_user_does_not_count_as_owner(owner):
    second = add_member(owner.organization, "owner", all_properties=True)
    second.user.is_active = False
    second.user.save(update_fields=["is_active"])
    with pytest.raises(ValidationError):
        services.set_member_active(owner, owner, False)


# --- roles ------------------------------------------------------------------------------------


def test_role_in_use_cannot_be_archived(owner):
    add_member(owner.organization, "caretaker")
    with pytest.raises(ValidationError):
        services.archive_role(owner, role(owner.organization, "caretaker"))


def test_unused_role_is_archived_and_hidden(owner):
    viewer = role(owner.organization, "viewer")
    services.archive_role(owner, viewer)
    assert not Role.objects.filter(pk=viewer.pk).exists()
    assert Role.all_objects.filter(pk=viewer.pk).exists()


def test_clone_role_copies_capabilities(owner):
    src = role(owner.organization, "caretaker")
    copy = services.clone_role(owner, src, name="Night caretaker")
    assert services.role_codenames(copy) == services.role_codenames(src)
    assert copy.organization_id == owner.organization_id


def test_role_changes_are_audited(owner):
    r = role(owner.organization, "caretaker")
    services.update_role(owner, r, capabilities=services.role_codenames(r) | {"prospects.view"})
    event = AuditEvent.objects.get(action="role.update", object_id=str(r.public_id))
    assert event.changes["capabilities"]["added"] == ["prospects.view"]


# --- property scope ---------------------------------------------------------------------------


def test_property_scoped_actor_cannot_manage_staff(owner):
    org = owner.organization
    p1, p2 = make_property(org), make_property(org)
    actor = add_member(org, "manager", properties=[p1])
    # staff.manage is org-wide, so even when granted it does nothing without all_properties.
    services.set_override(owner, actor, "staff.manage", True)
    target = add_member(org, "caretaker")
    with pytest.raises(PermissionDenied):
        services.set_property_scope(fresh(actor), target, all_properties=False, properties=[p2])


def test_set_property_scope_replaces_access(owner):
    org = owner.organization
    p1, p2 = make_property(org), make_property(org)
    m = add_member(org, "caretaker", properties=[p1])
    services.set_property_scope(owner, m, all_properties=False, properties=[p2])
    assert list(m.property_access.values_list("property_id", flat=True)) == [p2.pk]


# --- invitations ------------------------------------------------------------------------------


def test_invitation_round_trip(owner, outbox):
    org = owner.organization
    p = make_property(org)
    inv, token = services.invite_staff(owner, phone="0799 000 111", role=role(org, "caretaker"), properties=[p])
    assert inv.phone == "+254799000111"
    assert inv.token_hash != token  # only the hash is stored
    services.send_invitation(inv, f"https://example.test/invite/{token}/")
    assert outbox[-1][0] == "+254799000111"

    user = make_user(phone="+254799000111")
    m = services.accept_invitation(token, user)
    assert m.role.based_on_template.key == "caretaker"
    assert list(m.property_access.values_list("property_id", flat=True)) == [p.pk]
    with pytest.raises(ValidationError):
        services.accept_invitation(token, user)  # single use


def test_invitation_requires_matching_verified_phone(owner):
    _, token = services.invite_staff(owner, phone="0799000111", role=role(owner.organization, "viewer"))
    with pytest.raises(PermissionDenied):
        services.accept_invitation(token, make_user(phone="0799000222"))
    with pytest.raises(PermissionDenied):
        services.accept_invitation(token, make_user(phone="0799000111", verified=False))


def test_revoked_invitation_cannot_be_accepted(owner):
    inv, token = services.invite_staff(owner, phone="0799000111", role=role(owner.organization, "viewer"))
    services.revoke_invitation(owner, inv)
    with pytest.raises(ValidationError):
        services.accept_invitation(token, make_user(phone="0799000111"))


def test_cannot_invite_existing_member(owner):
    m = add_member(owner.organization, "viewer")
    with pytest.raises(ValidationError):
        services.invite_staff(owner, phone=m.user.phone, role=role(owner.organization, "viewer"))


def test_removed_member_can_rejoin_by_invitation(owner):
    m = add_member(owner.organization, "viewer")
    services.remove_member(owner, m)
    _, token = services.invite_staff(owner, phone=m.user.phone, role=role(owner.organization, "caretaker"))
    rejoined = services.accept_invitation(token, m.user)
    assert rejoined.pk == m.pk
    assert rejoined.is_active and not rejoined.is_archived
