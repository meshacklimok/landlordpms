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
