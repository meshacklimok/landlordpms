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


def _steps(client):
    return {s.key: s.done for s in client.get(reverse("accounts:home")).context["checklist"]}


def test_home_checklist_ticks_tenants(client, owner):
    assert not _steps(client)["tenant"]
    services.create_tenant(owner, name="X", phone="0712345678")
    assert _steps(client)["tenant"]


def test_list_shows_where_a_tenant_lives_and_filters_by_property(client, owner, tenant):
    import datetime

    from accounts.tests.factories import make_property
    from leases import services as lease_services
    from properties import services as property_services

    prop = make_property(owner.organization)
    block = property_services.create_building(owner, prop, name="Block A")
    unit = property_services.create_unit(owner, prop, code="B22", building=block)
    other = make_property(owner.organization, name="Elsewhere")
    lease = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=datetime.date(2026, 1, 1),
                                        end_date=None, rent=15000)
    lease_services.activate_lease(owner, lease)
    url = reverse("tenants:list")
    html = client.get(url).content.decode()
    assert "Block A" in html and "B22" in html
    assert [t.pk for t in client.get(url + f"?property={prop.public_id}").context["page"]] == [tenant.pk]
    assert list(client.get(url + f"?property={other.public_id}").context["page"]) == []


def test_add_tenant_with_a_unit_goes_to_its_lease_and_detail_offers_assign_and_move(client, owner):
    import datetime

    from accounts.tests.factories import make_property
    from leases import services as lease_services
    from properties import services as property_services

    prop = make_property(owner.organization)
    a1 = property_services.create_unit(owner, prop, code="A1")
    b2 = property_services.create_unit(owner, prop, code="B2")
    r = client.post(reverse("tenants:create"), {"kind": "INDIVIDUAL", "name": "Achieng", "phone": "0711000222",
                                                "unit": a1.pk, "language": "en"})
    t = Tenant.objects.get(name="Achieng")
    assert r.status_code == 302 and r.url == reverse("leases:create", args=[a1.public_id]) + f"?tenant={t.public_id}"
    detail = client.get(reverse("tenants:detail", args=[t.public_id]))
    assert detail.context["assign_units"] is not None and detail.context["move_form"] is None
    r = client.get(reverse("tenants:assign", args=[t.public_id]) + f"?unit={b2.public_id}")
    assert r.url == reverse("leases:create", args=[b2.public_id]) + f"?tenant={t.public_id}"
    lease = lease_services.create_lease(owner, unit=a1, tenants=[t], start_date=datetime.date(2026, 1, 1),
                                        end_date=None, rent=9000)
    lease_services.activate_lease(owner, lease)
    detail = client.get(reverse("tenants:detail", args=[t.public_id]))
    assert detail.context["assign_units"] is None and detail.context["move_lease"] == lease
    assert b"Move to another unit" in detail.content


def test_move_now_activates_the_new_lease_and_ends_the_old_one(client, owner):
    import datetime

    from accounts.tests.factories import make_property
    from leases import services as lease_services
    from leases.models import Lease
    from properties import services as property_services

    prop = make_property(owner.organization)
    a1 = property_services.create_unit(owner, prop, code="A1")
    b2 = property_services.create_unit(owner, prop, code="B2")
    t = services.create_tenant(owner, name="Achieng", phone="0711000222")
    old = lease_services.create_lease(owner, unit=a1, tenants=[t], start_date=datetime.date(2026, 1, 1),
                                      end_date=None, rent=9000)
    lease_services.activate_lease(owner, old)
    client.post(reverse("leases:detail", args=[old.public_id]), {
        "action": "transfer", "unit": b2.pk, "start_date": "2026-10-11", "activate_now": "on"})
    old.refresh_from_db()
    new = Lease.objects.get(unit=b2)
    assert new.status == Lease.Status.ACTIVE and old.status == Lease.Status.ENDED


def test_filter_tenants_with_no_unit_yet(client, owner, tenant):
    url = reverse("tenants:list")
    assert [t.pk for t in client.get(url + "?placed=none").context["page"]] == [tenant.pk]
    assert list(client.get(url + "?placed=yes").context["page"]) == []


def test_add_tenant_from_a_lease_form_returns_to_that_units_lease(client, owner):
    from accounts.tests.factories import make_property
    from properties import services as property_services

    prop = make_property(owner.organization)
    a1 = property_services.create_unit(owner, prop, code="A1")
    page = client.get(reverse("tenants:create") + f"?for_unit={a1.public_id}")
    assert b'name="for_unit"' in page.content and b"Assign to a unit" not in page.content
    assert b"More details" in page.content
    r = client.post(reverse("tenants:create"), {"kind": "INDIVIDUAL", "name": "Achieng", "phone": "0711000222",
                                                "for_unit": str(a1.public_id), "language": "en"})
    t = Tenant.objects.get(name="Achieng")
    assert r.url == reverse("leases:create", args=[a1.public_id]) + f"?tenant={t.public_id}"


def test_new_lease_shows_a_draft_banner_then_a_settling_in_checklist(client, owner):
    import datetime

    from accounts.tests.factories import make_property
    from leases import services as lease_services
    from properties import services as property_services

    prop = make_property(owner.organization)
    a1 = property_services.create_unit(owner, prop, code="A1")
    t = services.create_tenant(owner, name="Achieng", phone="0711000222")
    lease = lease_services.create_lease(owner, unit=a1, tenants=[t], start_date=datetime.date.today(), rent=9000,
                                        deposit_amount=9000)
    page = client.get(reverse("leases:detail", args=[lease.public_id]))
    assert b"bills nothing until you activate" in page.content and page.context["move_in_steps"] == []
    lease_services.activate_lease(owner, lease)
    steps = client.get(reverse("leases:detail", args=[lease.public_id])).context["move_in_steps"]
    labels = [s["label"] for s in steps]
    assert any("Invite Achieng" in label for label in labels)
    assert "Record the deposit" in labels and not any(s["done"] for s in steps)


def test_welcome_message_starts_with_house_rules_room(client, owner):
    import datetime

    from accounts.tests.factories import make_property
    from leases import services as lease_services
    from notifications import compose
    from properties import services as property_services

    client.force_login(owner.user)
    prop = make_property(owner.organization)
    a1 = property_services.create_unit(owner, prop, code="A1")
    t = services.create_tenant(owner, name="Achieng", phone="0711000222", email="a@example.com")
    lease = lease_services.create_lease(owner, unit=a1, tenants=[t], start_date=datetime.date.today(), rent=9000)
    lease_services.activate_lease(owner, lease)
    steps = client.get(reverse("leases:detail", args=[lease.public_id])).context["move_in_steps"]
    assert any("welcome message" in s["label"] and not s["done"] for s in steps)
    topic = compose.for_tenant(owner, t, welcome=True)
    subject, text = compose.draft(owner.organization, topic, t, "EMAIL")
    assert subject.startswith("Welcome to") and "House rules" in text and "Achieng" in text
    page = client.get(reverse("notifications:compose"), {"tenant": t.public_id, "welcome": 1})
    assert page.status_code == 200 and b"House rules" in page.content


def test_welcome_wording_is_kept_for_the_next_tenant(client, owner):
    import datetime

    from accounts.tests.factories import make_property
    from leases import services as lease_services
    from notifications import compose
    from properties import services as property_services

    prop = make_property(owner.organization)
    made = []
    for code, name, phone in (("A1", "Achieng", "0711000222"), ("A2", "Brian", "0711000333")):
        unit = property_services.create_unit(owner, prop, code=code)
        t = services.create_tenant(owner, name=name, phone=phone, email=f"{name}@example.com")
        lease = lease_services.create_lease(owner, unit=unit, tenants=[t], start_date=datetime.date.today(), rent=9000)
        lease_services.activate_lease(owner, lease)
        made.append((t, lease))
    first, lease = made[0]
    topic = compose.for_tenant(owner, first, welcome=True)
    compose.send(owner, topic, first, "EMAIL", subject="Welcome", text="Hi Achieng, quiet after 10pm. Ref A1 {x}")
    second = made[1][0]
    _, text = compose.draft(owner.organization, compose.for_tenant(owner, second, welcome=True), second, "EMAIL")
    assert text.startswith("Hi Brian, quiet after 10pm. Ref A2") and "Achieng" not in text
