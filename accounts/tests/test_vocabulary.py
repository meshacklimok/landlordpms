import pytest

from accounts.models import Organization
from accounts.vocabulary import RENTAL, vocabulary_for


def test_landlord_types_keep_the_rental_words():
    for kind in (Organization.Type.INDIVIDUAL, Organization.Type.COMPANY, Organization.Type.AGENCY):
        assert vocabulary_for(kind) is RENTAL


def test_other_billers_get_their_own_words():
    assert str(vocabulary_for(Organization.Type.ESTATE)["tenants"]) == "Members"
    assert str(vocabulary_for(Organization.Type.WATER)["unit"]) == "Connection"
    assert str(vocabulary_for(Organization.Type.WASTE)["property"]) == "Route"


def test_organization_vocab_follows_its_type():
    assert str(Organization(org_type=Organization.Type.ESTATE).vocab["rent"]) == "Service charge"
    assert str(Organization(org_type=Organization.Type.INDIVIDUAL).vocab["tenants"]) == "Tenants"


def test_only_the_new_billers_avoid_rent_words():
    assert Organization(org_type=Organization.Type.AGENCY).bills_rent
    assert not Organization(org_type=Organization.Type.WATER).bills_rent


@pytest.mark.django_db
@pytest.mark.parametrize("kind", [Organization.Type.WATER, Organization.Type.WASTE])
def test_new_route_and_water_plan_pages_open_for_water_and_garbage(client, kind):
    from django.urls import reverse

    from .factories import make_org, make_property
    from .test_isolation import login

    owner = make_org()
    owner.organization.org_type = kind
    owner.organization.save(update_fields=["org_type"])
    prop = make_property(owner.organization)
    login(client, owner)
    assert client.get(reverse("properties:create")).status_code == 200
    assert client.get(reverse("meters:water_plan", args=[prop.public_id])).status_code == 200


@pytest.mark.django_db
def test_setup_checklist_and_route_form_use_the_organizations_words(client):
    from django.urls import reverse

    from accounts import setup

    from .factories import make_org
    from .test_isolation import login

    owner = make_org()
    owner.organization.org_type = Organization.Type.WASTE
    owner.organization.save(update_fields=["org_type"])
    labels = [str(s.label) for s in setup.checklist(owner.organization)]
    assert "Add a route" in labels and "Add a client" in labels
    assert not any("tenant" in label.lower() or "property" in label.lower() for label in labels)
    login(client, owner)
    page = client.get(reverse("properties:create")).content.decode()
    assert "Apartment block" not in page and "your tenants" not in page
