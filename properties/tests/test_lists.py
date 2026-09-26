"""Unit lists: filters and counts run in the database, pages stay small, scope holds (doc 11 §20)."""

import datetime

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from leases import services as lease_services
from properties import selectors, services
from properties.models import Unit
from properties.views import PAGE_SIZE
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

TODAY = datetime.date.today()
UNITS = reverse("properties:units")


@pytest.fixture
def owner(client):
    m = make_org()
    login(client, m)
    return m


@pytest.fixture
def prop(owner):
    return services.create_property(owner, name="Greenview", code="GV")


@pytest.fixture
def mixed(owner, prop):
    """A1 occupied, A2 available, A3 reserved, A4 inactive."""
    a1, a2, a3, a4 = (services.create_unit(owner, prop, code=f"A{n}") for n in range(1, 5))
    tenant = tenant_services.create_tenant(owner, name="Wanjiku", phone="0712345678")
    lease = lease_services.create_lease(owner, unit=a1, tenants=[tenant], start_date=TODAY, end_date=None,
                                        rent=15000)
    lease_services.activate_lease(owner, lease)
    services.set_unit_status(owner, a3, Unit.ManualStatus.RESERVED)
    services.set_unit_status(owner, a4, Unit.ManualStatus.INACTIVE)
    return a1, a2, a3, a4


def codes(response, key="page"):
    return [u.code for u in response.context[key]]


def test_status_counts_and_filters(owner, prop, mixed):
    units = selectors.visible_units(owner)
    counts = selectors.status_counts(units)
    assert {key: n for key, _label, n in counts} == {"vacant": 1, "occupied": 1, "reserved": 1, "inactive": 1}
    assert [u.code for u in selectors.filter_units(units, status="vacant")] == ["A2"]
    assert [u.code for u in selectors.filter_units(units, status="occupied")] == ["A1"]
    assert [u.code for u in selectors.filter_units(units, q="gv-a3")] == ["A3"]


def test_units_page_filters(client, owner, prop, mixed):
    other = services.create_property(owner, name="Riverside", code="RS")
    services.create_unit(owner, other, code="S1", unit_type=Unit.Type.SHOP)
    assert codes(client.get(UNITS)) == ["A1", "A2", "A3", "A4", "S1"]
    assert codes(client.get(UNITS, {"status": "vacant"})) == ["A2", "S1"]
    assert codes(client.get(UNITS, {"property": str(other.public_id)})) == ["S1"]
    assert codes(client.get(UNITS, {"unit_type": "SHOP"})) == ["S1"]
    assert codes(client.get(UNITS, {"q": "a4"})) == ["A4"]
    # Junk filters are ignored, not errors.
    assert client.get(UNITS, {"property": "nope", "status": "x", "unit_type": "x", "page": "99"}).status_code == 200


def test_units_page_respects_scope(client, owner, prop, mixed):
    elsewhere = make_property(owner.organization)
    services.create_unit(owner, elsewhere, code="Z1")
    foreign = make_org()
    services.create_unit(foreign, make_property(foreign.organization), code="F1")

    agent = add_member(owner.organization, "leasing_agent", properties=[prop])
    login(client, agent)
    assert codes(client.get(UNITS)) == ["A1", "A2", "A3", "A4"]
    # A property outside the scope cannot be picked as a filter.
    assert codes(client.get(UNITS, {"property": str(elsewhere.public_id)})) == ["A1", "A2", "A3", "A4"]


def test_units_page_needs_login(client):
    assert client.get(UNITS).status_code == 302


def test_property_page_paginates_units(client, owner, prop):
    for n in range(PAGE_SIZE + 3):
        services.create_unit(owner, prop, code=f"U{n:03d}")
    url = reverse("properties:detail", args=[prop.public_id])
    r = client.get(url)
    assert r.context["unit_total"] == PAGE_SIZE + 3
    assert len(r.context["units"]) == PAGE_SIZE
    assert r.context["status_counts"] == [("vacant", "Available", PAGE_SIZE + 3)]
    assert len(client.get(url, {"page": 2}).context["units"]) == 3
    assert codes(client.get(url, {"q": "u007"}), "units") == ["U007"]


def test_property_page_status_filter(client, owner, prop, mixed):
    url = reverse("properties:detail", args=[prop.public_id])
    assert codes(client.get(url, {"status": "reserved"}), "units") == ["A3"]
