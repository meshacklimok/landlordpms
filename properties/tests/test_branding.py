"""Branch and branding fields (D-040, design-in): stored and checked, not yet used for scoping."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.urls import reverse

from accounts.models import Branch
from accounts.tests.factories import make_org, make_property
from properties import services

pytestmark = pytest.mark.django_db


def test_display_name_prefers_the_brand():
    org = make_org(name="Kamau Holdings").organization
    assert org.display_name == "Kamau Holdings"
    org.brand_name = "Kamau Homes"
    assert org.display_name == "Kamau Homes"


def test_brand_colour_must_be_hex():
    org = make_org().organization
    org.brand_color = "blue"
    with pytest.raises(ValidationError):
        org.full_clean()


def test_branch_names_are_unique_per_org():
    org = make_org().organization
    Branch.objects.create(organization=org, name="Westlands")
    Branch.objects.create(organization=make_org().organization, name="Westlands")
    with pytest.raises(IntegrityError):
        Branch.objects.create(organization=org, name="WESTLANDS")


def test_property_branch_must_be_same_org():
    org = make_org().organization
    prop = make_property(org)
    prop.branch = Branch.objects.create(organization=make_org().organization, name="Elsewhere")
    with pytest.raises(ValidationError):
        prop.full_clean()
    prop.branch = Branch.objects.create(organization=org, name="Westlands")
    prop.full_clean()


def test_vacancy_page_shows_the_brand(client):
    owner = make_org(name="Kamau Holdings")
    org = owner.organization
    org.brand_name = "Kamau Homes"
    org.save()
    prop = services.create_property(owner, name="Greenview", code="GV")
    unit = services.share_unit(owner, services.create_unit(owner, prop, code="A1"))
    assert "Kamau Homes" in client.get(reverse("vacancy", args=[unit.share_token])).content.decode()
