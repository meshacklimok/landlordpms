"""Cross-organization isolation (doc 11 §3): nothing in org A is reachable from org B."""

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from accounts import services
from accounts.middleware import SESSION_KEY
from accounts.models import Membership, Role
from accounts.permissions import can, visible_properties
from properties.models import Property

from .factories import PASSWORD, add_member, make_org, make_property, make_user, role

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_orgs():
    a, b = make_org("Alpha"), make_org("Bravo")
    return a, b


def test_for_org_refuses_none():
    with pytest.raises(ValueError):
        Property.objects.for_org(None)


def test_for_org_scopes(two_orgs):
    a, b = two_orgs
    pa, pb = make_property(a.organization), make_property(b.organization)
    assert list(Property.objects.for_org(a.organization)) == [pa]
    assert list(visible_properties(b)) == [pb]


def test_can_rejects_property_from_other_org(two_orgs):
    a, b = two_orgs
    assert not can(a, "properties.view", make_property(b.organization))


def test_property_access_row_pointing_at_other_org_is_ignored(two_orgs):
    a, b = two_orgs
    foreign = make_property(b.organization)
    m = add_member(a.organization, "caretaker", properties=[foreign])  # corrupt row
    assert list(visible_properties(m)) == []
    assert not can(m, "properties.view", foreign)


@pytest.mark.parametrize(
    "call",
    [
        lambda a, b: services.update_role(a, role(b.organization, "viewer"), name="x"),
        lambda a, b: services.archive_role(a, role(b.organization, "viewer")),
        lambda a, b: services.clone_role(a, role(b.organization, "viewer"), name="x"),
        lambda a, b: services.change_member_role(a, add_member(b.organization, "viewer"),
                                                 role(a.organization, "caretaker")),
        lambda a, b: services.change_member_role(a, add_member(a.organization, "viewer"),
                                                 role(b.organization, "caretaker")),
        lambda a, b: services.set_member_active(a, add_member(b.organization, "viewer"), False),
        lambda a, b: services.remove_member(a, add_member(b.organization, "viewer")),
        lambda a, b: services.set_override(a, add_member(b.organization, "viewer"), "tenants.view", True),
        lambda a, b: services.set_property_scope(a, add_member(b.organization, "viewer"), all_properties=True),
        lambda a, b: services.set_property_scope(a, add_member(a.organization, "viewer"), all_properties=False,
                                                 properties=[make_property(b.organization)]),
        lambda a, b: services.invite_staff(a, phone="0799000111", role=role(b.organization, "viewer")),
        lambda a, b: services.invite_staff(a, phone="0799000111", role=role(a.organization, "viewer"),
                                           properties=[make_property(b.organization)]),
    ],
    ids=["update-role", "archive-role", "clone-role", "member-role", "assign-foreign-role", "suspend",
         "remove", "override", "scope", "scope-foreign-property", "invite-foreign-role",
         "invite-foreign-property"],
)
def test_services_refuse_other_orgs_objects(two_orgs, call):
    a, b = two_orgs
    with pytest.raises(PermissionDenied):
        call(a, b)


def test_revoke_other_orgs_invitation(two_orgs):
    a, b = two_orgs
    inv, _ = services.invite_staff(b, phone="0799000111", role=role(b.organization, "viewer"))
    with pytest.raises(PermissionDenied):
        services.revoke_invitation(a, inv)


# --- HTTP ---------------------------------------------------------------------------------------


def login(client, membership):
    assert client.login(username=membership.user.phone, password=PASSWORD)
    session = client.session
    session[SESSION_KEY] = str(membership.organization.public_id)
    session.save()


def test_member_page_of_other_org_is_404(client, two_orgs):
    a, b = two_orgs
    login(client, a)
    foreign = add_member(b.organization, "viewer")
    assert client.get(reverse("accounts:member", args=[foreign.public_id])).status_code == 404
    assert client.post(reverse("accounts:member", args=[foreign.public_id]),
                       {"action": "suspend"}).status_code == 404
    assert Membership.objects.get(pk=foreign.pk).is_active


def test_role_page_of_other_org_is_404(client, two_orgs):
    a, b = two_orgs
    login(client, a)
    foreign = role(b.organization, "viewer")
    assert client.get(reverse("accounts:role_edit", args=[foreign.public_id])).status_code == 404
    assert client.post(reverse("accounts:role_archive", args=[foreign.public_id])).status_code == 404
    assert Role.objects.filter(pk=foreign.pk).exists()


def test_revoke_invitation_of_other_org_is_404(client, two_orgs):
    a, b = two_orgs
    inv, _ = services.invite_staff(b, phone="0799000111", role=role(b.organization, "viewer"))
    login(client, a)
    assert client.post(reverse("accounts:revoke_invite", args=[inv.public_id])).status_code == 404


def test_staff_list_shows_only_own_org(client, two_orgs):
    a, b = two_orgs
    login(client, a)
    body = client.get(reverse("accounts:staff")).content.decode()
    assert a.user.full_name in body
    assert b.user.full_name not in body


def test_session_cannot_select_org_user_is_not_in(client, two_orgs):
    a, b = two_orgs
    login(client, a)
    session = client.session
    session[SESSION_KEY] = str(b.organization.public_id)
    session.save()
    response = client.get(reverse("accounts:home"))
    assert response.wsgi_request.organization == a.organization


def test_switch_to_foreign_org_is_refused(client, two_orgs):
    a, b = two_orgs
    login(client, a)
    client.post(reverse("accounts:switch_org"), {"organization": str(b.organization.public_id)})
    assert client.get(reverse("accounts:home")).wsgi_request.organization == a.organization


def test_user_in_two_orgs_sees_the_active_one(client):
    user = make_user()
    a, b = make_org("Alpha", owner=user), make_org("Bravo", owner=user)
    pa = make_property(a.organization)
    make_property(b.organization)
    login(client, b)
    assert client.get(reverse("accounts:home")).wsgi_request.organization == b.organization
    assert pa not in visible_properties(b)
