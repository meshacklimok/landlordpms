"""Property pages: render, gate by capability and scope, and never leak across organizations."""

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org
from accounts.tests.test_isolation import login
from properties import services
from properties.models import Building, Property, Unit

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(client):
    m = make_org()
    login(client, m)
    return m


@pytest.fixture
def prop(owner):
    return services.create_property(owner, name="Greenview", code="GV")


def test_owner_pages_render(client, owner, prop):
    block = services.create_building(owner, prop, name="Block A")
    unit = services.create_unit(owner, prop, code="A1", building=block)
    for url in [
        reverse("properties:list"),
        reverse("properties:list") + "?archived=on",
        reverse("properties:list") + "?q=green",
        reverse("properties:create"),
        reverse("properties:detail", args=[prop.public_id]),
        reverse("properties:edit", args=[prop.public_id]),
        reverse("properties:unit_create", args=[prop.public_id]),
        reverse("properties:building_edit", args=[block.public_id]),
        reverse("properties:unit", args=[unit.public_id]),
        reverse("accounts:home"),
    ]:
        assert client.get(url).status_code == 200, url


def test_create_property_and_units_through_the_pages(client, owner):
    r = client.post(reverse("properties:create"), {"name": "Riverside", "code": "rs", "category": "APARTMENT_BLOCK",
                                                   "county": "KISUMU"})
    prop = Property.objects.get(code="RS")
    assert r.status_code == 302 and r["Location"] == reverse("properties:detail", args=[prop.public_id])

    r = client.post(reverse("properties:unit_create", args=[prop.public_id]),
                    {"code": "b2", "unit_type": "APARTMENT", "list_rent": "12000", "add_another": "1"})
    assert r["Location"] == reverse("properties:unit_create", args=[prop.public_id])
    assert Unit.objects.get(property=prop).payment_reference == "RS-B2"


def test_duplicate_code_shows_form_error(client, owner, prop):
    r = client.post(reverse("properties:create"), {"name": "Copy", "code": "GV", "category": "APARTMENT_BLOCK"})
    assert r.status_code == 200
    assert "code" in r.context["form"].errors


def test_duplicate_unit_code_shows_form_error(client, owner, prop):
    services.create_unit(owner, prop, code="A1")
    r = client.post(reverse("properties:unit_create", args=[prop.public_id]), {"code": "a1", "unit_type": "APARTMENT"})
    assert r.status_code == 200
    assert "code" in r.context["form"].errors


def test_edit_unit_status_and_archive_through_page(client, owner, prop):
    unit = services.create_unit(owner, prop, code="A1")
    url = reverse("properties:unit", args=[unit.public_id])
    client.post(url, {"code": "A9", "unit_type": "SHOP"})
    client.post(url, {"action": "status", "manual_status": "RESERVED"})
    unit.refresh_from_db()
    assert (unit.payment_reference, unit.unit_type, unit.manual_status) == ("GV-A9", "SHOP", "RESERVED")
    client.post(url, {"action": "archive"})
    assert Unit.all_objects.get(pk=unit.pk).is_archived
    assert client.get(url).status_code == 200  # archived unit is still reachable to restore
    client.post(url, {"action": "restore"})
    assert not Unit.all_objects.get(pk=unit.pk).is_archived


def test_add_and_archive_building_through_page(client, owner, prop):
    client.post(reverse("properties:building_create", args=[prop.public_id]), {"name": "Block B"})
    block = Building.objects.get(property=prop)
    client.post(reverse("properties:building_edit", args=[block.public_id]), {"action": "archive"})
    assert Building.all_objects.get(pk=block.pk).is_archived


def test_archive_and_restore_property_through_page(client, owner, prop):
    url = reverse("properties:archive", args=[prop.public_id])
    client.post(url)
    assert Property.all_objects.get(pk=prop.pk).is_archived
    assert client.get(reverse("properties:detail", args=[prop.public_id])).status_code == 200
    client.post(url, {"action": "restore"})
    assert not Property.all_objects.get(pk=prop.pk).is_archived


def test_caretaker_sees_but_cannot_change(client, owner, prop):
    unit = services.create_unit(owner, prop, code="A1")
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    client.logout()
    login(client, caretaker)
    assert client.get(reverse("properties:list")).status_code == 200
    detail = client.get(reverse("properties:detail", args=[prop.public_id]))
    assert detail.status_code == 200 and not detail.context["can_manage"]
    assert client.get(reverse("properties:unit", args=[unit.public_id])).context["form"] is None
    assert client.get(reverse("properties:create")).status_code == 403
    assert client.get(reverse("properties:edit", args=[prop.public_id])).status_code == 403
    assert client.post(reverse("properties:archive", args=[prop.public_id])).status_code == 403
    assert client.post(reverse("properties:unit", args=[unit.public_id]), {"action": "archive"}).status_code == 403
    assert not Unit.all_objects.get(pk=unit.pk).is_archived


def test_scoped_member_gets_404_for_properties_outside_scope(client, owner, prop):
    other = services.create_property(owner, name="Riverside", code="RS")
    hidden_unit = services.create_unit(owner, prop, code="A1")
    caretaker = add_member(owner.organization, "caretaker", properties=[other])
    client.logout()
    login(client, caretaker)
    assert [p.pk for p in client.get(reverse("properties:list")).context["page"]] == [other.pk]
    assert client.get(reverse("properties:detail", args=[prop.public_id])).status_code == 404
    assert client.get(reverse("properties:unit", args=[hidden_unit.public_id])).status_code == 404


def test_other_org_gets_404(client, owner, prop):
    unit = services.create_unit(owner, prop, code="A1")
    block = services.create_building(owner, prop, name="Block A")
    intruder = make_org("Intruder")
    client.logout()
    login(client, intruder)
    assert list(client.get(reverse("properties:list")).context["page"]) == []
    for method, url in [
        ("get", reverse("properties:detail", args=[prop.public_id])),
        ("get", reverse("properties:edit", args=[prop.public_id])),
        ("post", reverse("properties:archive", args=[prop.public_id])),
        ("post", reverse("properties:building_create", args=[prop.public_id])),
        ("get", reverse("properties:unit_create", args=[prop.public_id])),
        ("get", reverse("properties:building_edit", args=[block.public_id])),
        ("get", reverse("properties:unit", args=[unit.public_id])),
        ("post", reverse("properties:unit", args=[unit.public_id])),
    ]:
        assert getattr(client, method)(url).status_code == 404, url
    assert not Property.all_objects.get(pk=prop.pk).is_archived


def test_home_checklist_ticks_units(client, owner, prop):
    checklist = dict(client.get(reverse("accounts:home")).context["checklist"])
    assert not checklist["Add units"]
    services.create_unit(owner, prop, code="A1")
    checklist = dict(client.get(reverse("accounts:home")).context["checklist"])
    assert checklist["Add a property"] and checklist["Add units"]
