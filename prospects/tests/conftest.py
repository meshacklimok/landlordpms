import datetime

import pytest
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from properties import services as property_services
from prospects import services

TODAY = datetime.date(2026, 3, 2)
NOW = timezone.make_aware(datetime.datetime(2026, 3, 2, 9, 0))


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def prop(org):
    return make_property(org, name="Acacia Court")


@pytest.fixture
def unit(owner, prop):
    """A vacant unit, free to hold."""
    return property_services.create_unit(owner, prop, code="B1")


@pytest.fixture
def unit2(owner, prop):
    return property_services.create_unit(owner, prop, code="B2")


@pytest.fixture
def agent(org, prop):
    return add_member(org, "leasing_agent", properties=[prop])


@pytest.fixture
def prospect(owner, prop, unit):
    return services.create_prospect(owner, prop, name="Wanjiku Kamau", phone="0712345678", unit=unit)
