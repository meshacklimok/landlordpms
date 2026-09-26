"""Tenant pages: render, gate by capability, hide sensitive fields, never leak across organizations."""

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org
from accounts.tests.test_isolation import login
from tenants import services
from tenants.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner(client):
    m = make_org()
    login(client, m)
    return m


@pytest.fixture
def tenant(owner):
    return services.create_tenant(owner, name="Wanjiku Kamau", phone="0712345678", id_type="NATIONAL_ID",
                                  id_number="29876543", notes="Pays early")


def test_owner_pages_render(client, owner, tenant):
    for url in [
        reverse("tenants:list"),
        reverse("tenants:list") + "?q=wanjiku",
        reverse("tenants:list") + "?q=0712345678",
        reverse("tenants:list") + "?status=PROSPECT&archived=on",
        reverse("tenants:create"),
        reverse("tenants:detail", args=[tenant.public_id]),
        reverse("tenants:edit", args=[tenant.public_id]),
    ]:
        assert client.get(url).status_code == 200, url


def test_search_by_local_phone_format(client, owner, tenant):
    services.create_tenant(owner, name="Someone else", phone="0799000111")
    page = client.get(reverse("tenants:list") + "?q=0712 345 678").context["page"]
    assert [t.pk for t in page] == [tenant.pk]


def test_create_through_page(client, owner):
    r = client.post(reverse("tenants:create"), {"kind": "INDIVIDUAL", "name": "Otieno", "phone": "0722 000111",
                                                "notes": "Line one\nLine two"})
    t = Tenant.objects.get(name="Otieno")
    assert r["Location"] == reverse("tenants:detail", args=[t.public_id])
    assert (t.phone, t.notes) == ("+254722000111", "Line one\nLine two")


def test_duplicate_phone_warns_then_saves_on_confirm(client, owner, tenant):
    data = {"kind": "INDIVIDUAL", "name": "Husband", "phone": "+254 712 345678"}
    r = client.post(reverse("tenants:create"), data)
    assert r.status_code == 200 and r.context["duplicates"] == [tenant]
    assert not Tenant.objects.filter(name="Husband").exists()
    r = client.post(reverse("tenants:create"), {**data, "confirm_duplicate": "on"})
    assert r.status_code == 302 and Tenant.objects.filter(name="Husband").exists()


def test_edit_without_phone_change_does_not_warn(client, owner, tenant):
    services.create_tenant(owner, name="Husband", phone="0712345678")
    r = client.post(reverse("tenants:edit", args=[tenant.public_id]),
                    {"kind": "INDIVIDUAL", "name": "Wanjiku K.", "phone": "0712345678",
                     "id_type": "NATIONAL_ID", "id_number": "29876543"})
    assert r.status_code == 302
    assert Tenant.objects.get(pk=tenant.pk).name == "Wanjiku K."


def test_bad_kra_pin_shows_form_error(client, owner):
    r = client.post(reverse("tenants:create"), {"kind": "INDIVIDUAL", "name": "X", "phone": "0712345678",
                                                "kra_pin": "nope"})
    assert r.status_code == 200 and "kra_pin" in r.context["form"].errors


def test_archive_and_restore_through_page(client, owner, tenant):
    url = reverse("tenants:archive", args=[tenant.public_id])
    client.post(url)
    assert Tenant.all_objects.get(pk=tenant.pk).is_archived
    assert client.get(reverse("tenants:detail", args=[tenant.public_id])).status_code == 200
    client.post(url, {"action": "restore"})
    assert not Tenant.all_objects.get(pk=tenant.pk).is_archived


def test_sensitive_fields_hidden_without_capability(client, owner, tenant):
    agent = add_member(owner.organization, "leasing_agent", all_properties=True)
    client.logout()
    login(client, agent)
    detail = client.get(reverse("tenants:detail", args=[tenant.public_id]))
    assert b"29876543" not in detail.content
    form = client.get(reverse("tenants:edit", args=[tenant.public_id])).context["form"]
    assert "id_number" not in form.fields
    # Saving the edit form must not wipe the ID it cannot see.
    client.post(reverse("tenants:edit", args=[tenant.public_id]),
                {"kind": "INDIVIDUAL", "name": "Wanjiku", "phone": "0712345678"})
    tenant.refresh_from_db()
    assert (tenant.name, tenant.id_number) == ("Wanjiku", "29876543")
    # Searching by ID number does not work without the capability either.
    assert list(client.get(reverse("tenants:list") + "?q=29876543").context["page"]) == []


def test_caretaker_sees_but_cannot_change(client, owner, tenant):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    client.logout()
    login(client, caretaker)
    detail = client.get(reverse("tenants:detail", args=[tenant.public_id]))
    assert detail.status_code == 200 and not detail.context["can_manage"]
    assert client.get(reverse("tenants:create")).status_code == 403
    assert client.get(reverse("tenants:edit", args=[tenant.public_id])).status_code == 403
    assert client.post(reverse("tenants:archive", args=[tenant.public_id])).status_code == 403


def test_scoped_member_gets_404_for_tenants_they_cannot_see(client, owner, tenant):
    agent = add_member(owner.organization, "leasing_agent")
    client.logout()
    login(client, agent)
    assert list(client.get(reverse("tenants:list")).context["page"]) == []
    assert client.get(reverse("tenants:detail", args=[tenant.public_id])).status_code == 404
    # The duplicate warning must not reveal tenants the member cannot see.
    r = client.post(reverse("tenants:create"), {"kind": "INDIVIDUAL", "name": "New", "phone": "0712345678"})
    assert r.status_code == 302


def test_other_org_gets_404(client, owner, tenant):
    intruder = make_org("Intruder")
    client.logout()
    login(client, intruder)
    assert list(client.get(reverse("tenants:list") + "?q=0712345678").context["page"]) == []
    for method, url in [
        ("get", reverse("tenants:detail", args=[tenant.public_id])),
        ("get", reverse("tenants:edit", args=[tenant.public_id])),
        ("post", reverse("tenants:edit", args=[tenant.public_id])),
        ("post", reverse("tenants:archive", args=[tenant.public_id])),
    ]:
        assert getattr(client, method)(url).status_code == 404, url
    assert not Tenant.all_objects.get(pk=tenant.pk).is_archived


def test_home_checklist_ticks_tenants(client, owner):
    assert not dict(client.get(reverse("accounts:home")).context["checklist"])["Add tenants"]
    services.create_tenant(owner, name="X", phone="0712345678")
    assert dict(client.get(reverse("accounts:home")).context["checklist"])["Add tenants"]
