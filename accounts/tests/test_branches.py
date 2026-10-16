"""Branches that limit what staff see (D-127)."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts import branches, services
from accounts.models import Branch
from accounts.permissions import accessible_property_ids, can, visible_properties
from properties import services as property_services
from properties.models import Property

from .factories import add_member, fresh, make_org, make_property, make_user, role
from .test_isolation import login

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def westlands(owner):
    return branches.create_branch(owner, name="Westlands", phone="0722000111")


def _in(branch, name=None):
    prop = make_property(branch.organization, name)
    Property.objects.filter(pk=prop.pk).update(branch=branch)
    return prop


def test_branch_gives_every_property_in_it_including_later_ones(owner, westlands):
    first = _in(westlands)
    other = make_property(owner.organization)
    m = add_member(owner.organization, "manager")
    services.set_property_scope(owner, m, all_properties=False, branches=[westlands])
    m = fresh(m)
    assert accessible_property_ids(m) == {first.pk}
    later = _in(westlands)
    m = fresh(m)
    assert set(visible_properties(m).values_list("pk", flat=True)) == {first.pk, later.pk}
    assert can(m, "properties.view", later)
    assert not can(m, "properties.view", other)


def test_branch_and_single_properties_add_up(owner, westlands):
    a, b = _in(westlands), make_property(owner.organization)
    m = add_member(owner.organization, "caretaker", properties=[b])
    services.set_property_scope(owner, m, all_properties=False, properties=[b], branches=[westlands])
    assert accessible_property_ids(fresh(m)) == {a.pk, b.pk}


def test_archived_branch_gives_nothing(owner, westlands):
    m = add_member(owner.organization, "manager")
    services.set_property_scope(owner, m, all_properties=False, branches=[westlands])
    branches.archive_branch(owner, westlands)
    assert accessible_property_ids(fresh(m)) == set()


def test_cannot_archive_branch_with_properties(owner, westlands):
    _in(westlands)
    with pytest.raises(ValidationError):
        branches.archive_branch(owner, westlands)
    westlands.refresh_from_db()
    assert not westlands.is_archived


def test_scoped_staff_cannot_hand_out_a_branch_they_lack(owner, westlands):
    karen = branches.create_branch(owner, name="Karen")
    admin = add_member(owner.organization, "manager")
    services.set_override(owner, admin, "staff.manage", True)
    services.set_property_scope(owner, admin, all_properties=False, branches=[westlands])
    admin = fresh(admin)
    m = add_member(owner.organization, "caretaker")
    with pytest.raises(PermissionDenied):
        services.set_property_scope(admin, m, all_properties=False, branches=[karen])
    with pytest.raises(PermissionDenied):
        services.invite_staff(admin, phone="0799000333", role=role(owner.organization, "caretaker"),
                              branches=[karen])


def test_branch_of_another_org_is_refused(owner, westlands):
    other = make_org()
    m = add_member(other.organization, "manager")
    with pytest.raises(PermissionDenied):
        services.set_property_scope(other, m, all_properties=False, branches=[westlands])


def test_invitation_carries_branches(owner, westlands):
    a = _in(westlands)
    _, token = services.invite_staff(owner, phone="0799000444", role=role(owner.organization, "caretaker"),
                                     branches=[westlands])
    m = services.accept_invitation(token, make_user(phone="0799000444"))
    assert list(m.branches.all()) == [westlands]
    assert accessible_property_ids(fresh(m)) == {a.pk}


def test_branch_names_are_unique_and_phone_is_normalised(owner, westlands):
    assert westlands.phone == "+254722000111"
    with pytest.raises(ValidationError):
        branches.create_branch(owner, name="westlands")


def test_only_organization_managers_handle_branches(owner, westlands):
    manager = add_member(owner.organization, "manager", all_properties=True)
    with pytest.raises(PermissionDenied):
        branches.create_branch(manager, name="Karen")


def test_moving_a_property_between_branches(owner, westlands):
    prop = make_property(owner.organization)
    m = add_member(owner.organization, "manager")
    services.set_property_scope(owner, m, all_properties=False, branches=[westlands])
    property_services.update_property(owner, prop, branch=westlands)
    assert accessible_property_ids(fresh(m)) == {prop.pk}
    branches.archive_branch(owner, branches.create_branch(owner, name="Old"))
    old = Branch.all_objects.get(name="Old")
    with pytest.raises(ValidationError):
        property_services.update_property(owner, prop, branch=old)


def test_only_organization_managers_move_a_property(owner, westlands):
    prop = make_property(owner.organization)
    manager = add_member(owner.organization, "manager", all_properties=True)
    with pytest.raises(PermissionDenied):
        property_services.update_property(manager, prop, branch=westlands)
    property_services.update_property(manager, prop, name="Renamed")  # other fields still fine


def test_branch_pages(client, owner, westlands):
    login(client, owner)
    _in(westlands, "Acacia Court")
    page = client.get(reverse("accounts:branches")).content.decode()
    assert "Westlands" in page
    assert client.post(reverse("accounts:branches"), {"name": "Karen"}).status_code == 302
    assert Branch.objects.filter(name="Karen").exists()
    url = reverse("accounts:branch", args=[westlands.public_id])
    assert "Acacia Court" in client.get(url).content.decode()
    client.post(url, {"action": "save", "name": "Westlands Office", "phone": "", "email": ""})
    westlands.refresh_from_db()
    assert westlands.name == "Westlands Office"
    client.post(url, {"action": "archive"})
    westlands.refresh_from_db()
    assert not westlands.is_archived  # still has a property


def test_property_list_filters_by_branch(client, owner, westlands):
    login(client, owner)
    karen = branches.create_branch(owner, name="Karen")
    _in(westlands, "Acacia Court")
    _in(karen, "Baobab Flats")
    page = client.get(reverse("properties:list"), {"branch": str(karen.public_id)}).content.decode()
    assert "Baobab Flats" in page and "Acacia Court" not in page


def test_branch_pages_need_organization_manage(client, owner, westlands):
    manager = add_member(owner.organization, "manager", all_properties=True)
    login(client, manager)
    assert client.get(reverse("accounts:branches")).status_code == 403
