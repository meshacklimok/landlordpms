"""A landlord with one property is not shown agency or portfolio tools (D-150); they return when they apply."""

import pytest
from django.test import Client
from django.urls import reverse

from accounts import services
from accounts.models import Organization
from accounts.tests.factories import make_user
from properties import services as property_services
from properties.models import Property

pytestmark = pytest.mark.django_db


def solo(org_type=Organization.Type.INDIVIDUAL):
    user = make_user()
    owner = services.create_organization(user=user, name="Solo", org_type=org_type)
    prop = property_services.create_property(owner, name="My Flat", code="MF", category=Property.Category.SINGLE_HOUSE)
    client = Client()
    client.force_login(user)
    return owner, prop, client


def labels(client):
    return {r["label"] for r in client.get(reverse("reports:centre")).context["reports"]}


def test_one_flat_landlord_does_not_see_fees_or_owner_statements():
    owner, prop, client = solo()
    shown = labels(client)
    assert {"Dashboard", "Unit by unit", "Profit and loss", "Aged receivables", "Property scorecard"} <= shown
    assert not {"Management fees", "Owner statement"} & shown
    page = client.get(reverse("properties:list")).content.decode()
    assert reverse("properties:owners") not in page
    form = client.get(reverse("properties:edit", args=[prop.public_id])).content.decode()
    assert 'name="owner"' not in form and "management_fee" not in form and 'name="name"' in form
    assert 'name="property"' not in client.get(reverse("properties:units")).content.decode()
    assert 'name="property"' not in client.get(reverse("reports:dashboard")).content.decode()
    assert "Set the owner" not in client.get(reverse("properties:detail", args=[prop.public_id])).content.decode()


def test_the_first_property_is_added_without_them():
    user = make_user()
    services.create_organization(user=user, name="New", org_type=Organization.Type.INDIVIDUAL)
    client = Client()
    client.force_login(user)
    form = client.get(reverse("properties:create")).content.decode()
    assert 'name="owner"' not in form and "management_fee" not in form


def test_a_second_property_brings_them_back():
    owner, prop, client = solo()
    # The form for the second property already offers the owner and fee fields.
    assert 'name="owner"' in client.get(reverse("properties:create")).content.decode()
    property_services.create_property(owner, name="Block B", code="BB")
    assert {"Management fees", "Owner statement"} <= labels(client)
    assert 'name="property"' in client.get(reverse("properties:units")).content.decode()
    assert reverse("properties:owners") in client.get(reverse("properties:list")).content.decode()


def test_an_owner_or_a_fee_on_the_one_property_keeps_the_tools():
    owner, prop, client = solo()
    property_services.update_property(owner, prop, management_fee_percent=10)
    assert "Management fees" in labels(client)


def test_companies_and_agencies_always_see_them():
    for kind in (Organization.Type.COMPANY, Organization.Type.AGENCY):
        owner, prop, client = solo(kind)
        assert {"Management fees", "Owner statement"} <= labels(client)
        assert 'name="owner"' in client.get(reverse("properties:create")).content.decode()


def test_saving_the_one_property_without_the_agency_fields_works():
    owner, prop, client = solo()
    r = client.post(reverse("properties:edit", args=[prop.public_id]), {"name": "My Flat Renamed", "category": "SINGLE_HOUSE"})
    assert r.status_code == 302
    prop.refresh_from_db()
    assert prop.name == "My Flat Renamed" and prop.owner is None
