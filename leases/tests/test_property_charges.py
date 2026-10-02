"""D-069: one recurring charge added to every open lease at a property."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from billing.models import ChargeType
from billing.services import ensure_default_charge_types
from leases import services
from leases.models import LeaseCharge
from properties import services as property_services
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

D = datetime.date
JAN = D(2026, 1, 1)


@pytest.fixture
def owner(client):
    m = make_org()
    login(client, m)
    return m


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


@pytest.fixture
def garbage(owner):
    ensure_default_charge_types(owner.organization)
    return ChargeType.objects.get(organization=owner.organization, category=ChargeType.Category.GARBAGE)


def lease_on(owner, prop, code, n, *, start=JAN, end=None, active=True):
    unit = property_services.create_unit(owner, prop, code=code)
    tenant = tenant_services.create_tenant(owner, name=f"Tenant {code}", phone=f"07120000{n:02d}")
    lease = services.create_lease(owner, unit=unit, tenants=[tenant], start_date=start, end_date=end, rent=15000)
    return services.activate_lease(owner, lease) if active else lease


def test_it_goes_on_every_open_lease_that_can_take_it(owner, prop, garbage):
    a1 = lease_on(owner, prop, "A1", 1)
    a2 = lease_on(owner, prop, "A2", 2)
    draft = lease_on(owner, prop, "A3", 3, start=D(2030, 6, 1), active=False)
    short = lease_on(owner, prop, "A4", 4, end=D(2030, 3, 31))
    services.add_charge(owner, a2, charge_type=garbage, amount=300)
    other = lease_on(owner, make_property(owner.organization), "B1", 5)

    added, skipped = services.add_charge_to_property(owner, prop, charge_type=garbage, amount=500,
                                                     active_from=D(2030, 4, 1))
    assert {c.lease for c in added} == {a1, draft}
    assert {lease: reason for lease, reason in skipped}.keys() == {a2, short}
    by_lease = {c.lease: c for c in added}
    assert by_lease[a1].active_from == D(2030, 4, 1) and by_lease[a1].amount == Decimal("500")
    assert by_lease[draft].active_from == D(2030, 6, 1)
    assert not LeaseCharge.objects.filter(lease=other).exists()
    assert LeaseCharge.objects.get(lease=a2).amount == Decimal("300")


def test_the_plan_saves_nothing(owner, prop, garbage):
    lease_on(owner, prop, "A1", 1)
    to_add, skipped = services.plan_property_charge(owner, prop, charge_type=garbage, active_from=D(2026, 4, 1))
    assert len(to_add) == 1 and not skipped
    assert not LeaseCharge.objects.exists()


def test_rent_and_other_orgs_types_are_refused(owner, prop):
    lease_on(owner, prop, "A1", 1)
    ensure_default_charge_types(owner.organization)
    rent = ChargeType.objects.get(organization=owner.organization, category=ChargeType.Category.RENT)
    with pytest.raises(ValidationError):
        services.add_charge_to_property(owner, prop, charge_type=rent, amount=500, active_from=D(2026, 4, 1))
    foreign = make_org().organization
    ensure_default_charge_types(foreign)
    theirs = ChargeType.objects.get(organization=foreign, category=ChargeType.Category.GARBAGE)
    with pytest.raises(PermissionDenied):
        services.add_charge_to_property(owner, prop, charge_type=theirs, amount=500, active_from=D(2026, 4, 1))
    assert not LeaseCharge.objects.exists()


def test_it_needs_charges_manage(owner, prop, garbage):
    lease_on(owner, prop, "A1", 1)
    viewer = add_member(owner.organization, "viewer", all_properties=True)
    with pytest.raises(PermissionDenied):
        services.add_charge_to_property(viewer, prop, charge_type=garbage, amount=500, active_from=D(2026, 4, 1))


def test_page_previews_then_adds(client, owner, prop, garbage):
    a1 = lease_on(owner, prop, "A1", 1)
    detail = client.get(reverse("properties:detail", args=[prop.public_id]))
    url = reverse("properties:charges", args=[prop.public_id])
    assert detail.context["can_add_charges"] and url.encode() in detail.content

    page = client.get(url)
    assert page.status_code == 200 and page.context["form"].initial["charge_type"] == garbage

    data = {"charge_type": garbage.pk, "amount": "500", "active_from": "2026-04-01"}
    preview = client.post(url, {**data, "action": "preview"})
    assert preview.status_code == 200 and [lease for lease, _ in preview.context["to_add"]] == [a1]
    assert b"Add to 1 lease" in preview.content
    assert not LeaseCharge.objects.exists()

    done = client.post(url, {**data, "action": "apply"})
    assert done.status_code == 302
    assert LeaseCharge.objects.get(lease=a1).amount == Decimal("500")


def test_page_is_refused_without_the_capability(client, owner, prop, garbage):
    lease_on(owner, prop, "A1", 1)
    viewer = add_member(owner.organization, "viewer", all_properties=True)
    login(client, viewer)
    assert not client.get(reverse("properties:detail", args=[prop.public_id])).context["can_add_charges"]
    assert client.get(reverse("properties:charges", args=[prop.public_id])).status_code == 403


def test_no_button_without_open_leases(client, owner, prop):
    assert not client.get(reverse("properties:detail", args=[prop.public_id])).context["can_add_charges"]
