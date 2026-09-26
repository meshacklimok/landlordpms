"""Lease pages: render, gate by capability and property scope, never leak across organizations."""

import datetime

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from billing.models import ChargeType
from billing.services import ensure_default_charge_types
from leases import services
from leases.models import Lease
from properties import services as property_services
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

D = datetime.date


@pytest.fixture
def owner(client):
    m = make_org()
    login(client, m)
    return m


@pytest.fixture
def unit(owner):
    return property_services.create_unit(owner, make_property(owner.organization), code="A1")


@pytest.fixture
def tenant(owner):
    return tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")


@pytest.fixture
def lease(owner, unit, tenant):
    return services.create_lease(owner, unit=unit, tenants=[tenant], start_date=D(2026, 1, 1),
                                 end_date=D(2026, 12, 31), rent=15000)


def detail(lease):
    return reverse("leases:detail", args=[lease.public_id])


def test_owner_pages_render(client, owner, unit, tenant, lease):
    for url in [
        reverse("leases:list"),
        reverse("leases:list") + "?q=wanjiku&status=DRAFT",
        reverse("leases:create", args=[unit.public_id]),
        reverse("leases:create", args=[unit.public_id]) + f"?tenant={tenant.public_id}",
        detail(lease),
        reverse("leases:edit", args=[lease.public_id]),
        reverse("properties:unit", args=[unit.public_id]),
        reverse("properties:detail", args=[unit.property.public_id]),
        reverse("tenants:detail", args=[tenant.public_id]),
        reverse("billing:charge_types"),
        reverse("accounts:home"),
    ]:
        assert client.get(url).status_code == 200, url


def test_create_through_page(client, owner, unit, tenant):
    other = tenant_services.create_tenant(owner, name="Otieno", phone="0722000111")
    r = client.post(reverse("leases:create", args=[unit.public_id]), {
        "tenant": tenant.pk, "co_tenants": [other.pk, tenant.pk], "start_date": "2026-03-01", "end_date": "",
        "rent": "12000", "deposit_amount": "12000", "due_day": "5", "grace_days": "3", "notice_days": "30",
        "terms": "No pets",
    })
    lease = Lease.objects.get()
    assert r["Location"] == detail(lease)
    assert lease.end_date is None and lease.due_day == 5
    assert lease.primary_tenant == tenant
    assert lease.lease_tenants.count() == 2


def test_create_shows_service_errors(client, owner, unit, tenant):
    r = client.post(reverse("leases:create", args=[unit.public_id]), {
        "tenant": tenant.pk, "start_date": "2026-03-01", "end_date": "2026-02-01", "rent": "12000",
        "deposit_amount": "0", "due_day": "1", "grace_days": "3", "notice_days": "30",
    })
    assert r.status_code == 200
    assert r.context["form"].errors["end_date"]
    assert not Lease.objects.exists()


def test_edit_draft(client, owner, lease):
    r = client.post(reverse("leases:edit", args=[lease.public_id]), {
        "start_date": "2026-01-01", "end_date": "2026-12-31", "rent": "16000", "deposit_amount": "0",
        "due_day": "1", "grace_days": "3", "notice_days": "30",
    })
    assert r.status_code == 302
    assert lease.rent_on(D(2026, 1, 1)) == 16000


def test_detail_actions(client, owner, lease):
    org = owner.organization
    ensure_default_charge_types(org)
    water = ChargeType.objects.get(organization=org, category=ChargeType.Category.WATER)
    url = detail(lease)
    client.post(url, {"action": "add_charge", "charge_type": water.pk, "amount": "500"})
    assert lease.charges.get().amount == 500
    client.post(url, {"action": "add_payer", "phone": "0799111222", "name": "Employer"})
    payer = lease.payers.get()
    other = tenant_services.create_tenant(owner, name="Otieno", phone="0722000111")
    client.post(url, {"action": "add_tenant", "tenant": other.pk})
    client.post(url, {"action": "make_primary", "tenant": other.public_id})
    assert lease.lease_tenants.get(is_primary=True).tenant == other
    client.post(url, {"action": "remove_payer", "payer": payer.pk})
    client.post(url, {"action": "end_charge", "charge": lease.charges.get().pk})
    assert not lease.payers.exists() and not lease.charges.exists()
    # A bad payer phone re-renders with the error.
    r = client.post(url, {"action": "add_payer", "phone": "12"})
    assert r.status_code == 200 and r.context["payer_form"].errors
    r = client.post(url, {"action": "delete"})
    assert r["Location"] == reverse("properties:unit", args=[lease.unit.public_id])
    assert not Lease.objects.exists()


def test_rent_change_through_page(client, owner, lease):
    Lease.objects.filter(pk=lease.pk).update(status=Lease.Status.ACTIVE)
    r = client.get(detail(lease))
    assert r.context["rent_form"] is not None and not r.context["can_edit_draft"]
    client.post(detail(lease), {"action": "rent_change", "effective_from": "2026-07-01", "amount": "17000"})
    assert lease.rent_on(D(2026, 7, 1)) == 17000
    r = client.post(detail(lease), {"action": "rent_change", "effective_from": "2025-07-01", "amount": "17000"})
    assert r.context["rent_form"].errors["effective_from"]
    assert client.get(reverse("leases:edit", args=[lease.public_id])).status_code == 302


def test_viewer_sees_but_cannot_change(client, owner, unit, lease):
    viewer = add_member(owner.organization, "viewer", all_properties=True)
    login(client, viewer)
    r = client.get(detail(lease))
    assert r.status_code == 200
    assert not any(r.context[k] for k in ("can_edit_draft", "can_charges", "can_payers", "can_change_rent"))
    assert client.get(reverse("leases:create", args=[unit.public_id])).status_code == 403
    assert client.post(detail(lease), {"action": "delete"}).status_code == 403
    assert Lease.objects.filter(pk=lease.pk).exists()
    assert client.get(reverse("billing:charge_types")).status_code == 403


def test_out_of_scope_and_other_org_are_404(client, owner, unit, lease):
    scoped = add_member(owner.organization, "leasing_agent", properties=[make_property(owner.organization)])
    login(client, scoped)
    assert client.get(detail(lease)).status_code == 404
    assert client.get(reverse("leases:create", args=[unit.public_id])).status_code == 404
    assert lease not in client.get(reverse("leases:list")).context["page"]
    login(client, make_org())
    assert client.get(detail(lease)).status_code == 404
    assert client.get(reverse("leases:edit", args=[lease.public_id])).status_code == 404


def test_charge_types_page(client, owner):
    url = reverse("billing:charge_types")
    client.post(url, {"action": "create", "name": "Parking bay", "category": "PARKING"})
    ct = ChargeType.objects.get(name="Parking bay")
    client.post(url, {"action": "rename", "charge_type": ct.public_id, "name": "Parking"})
    client.post(url, {"action": "archive", "charge_type": ct.public_id})
    ct.refresh_from_db()
    assert ct.name == "Parking" and ct.is_archived
    r = client.post(url, {"action": "create", "name": "Rent 2", "category": "RENT"})
    assert r.context["form"].errors["category"]
