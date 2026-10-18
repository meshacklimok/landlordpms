"""The rent outlook (D-172): contracted rent, rent ending, expected collection."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from leases.models import Lease, LeaseRentChange
from reports import outlook
from reports.tests.test_income import make_lease

pytestmark = pytest.mark.django_db

D = datetime.date
TODAY = D(2026, 3, 20)


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


def lease(owner, prop, code, *, start=D(2025, 1, 1), end=None, **fields):
    obj = make_lease(owner, prop, code=code, start=start)
    Lease.objects.filter(pk=obj.pk).update(end_date=end, **fields)
    obj.refresh_from_db()
    return obj


def report(owner, **kw):
    return outlook.outlook(owner, today=TODAY, **kw)


def test_nothing(owner):
    r = report(owner)
    assert [m.contracted for m in r.months] == [0] * 6 and r.ending == [] and r.collection_rate is None
    assert r.months[0].month == D(2026, 3, 1) and r.months[-1].month == D(2026, 8, 1)


def test_contracted_rent_runs_until_the_end_date(owner, prop):
    lease(owner, prop, "A1")  # 20,000 and no end
    lease(owner, prop, "A2", end=D(2026, 5, 31))  # last rent due 5 May
    r = report(owner)
    assert [m.contracted for m in r.months] == [40000, 40000, 40000, 20000, 20000, 20000]
    assert [m.leases for m in r.months] == [2, 2, 2, 1, 1, 1]
    assert [m.ending for m in r.months] == [0, 0, 20000, 0, 0, 0]
    assert r.ending_total == 20000 and r.ending[0].why == "end date" and r.ending[0].days == 72


def test_notice_to_vacate_and_closed_leases_end_rent_earlier(owner, prop):
    lease(owner, prop, "B1", move_out_on=D(2026, 4, 30))
    lease(owner, prop, "B2", ended_on=D(2026, 6, 10), status="ENDED")
    r = report(owner)
    assert [m.contracted for m in r.months] == [40000, 40000, 20000, 20000, 0, 0]
    assert [m.ending for m in r.months] == [0, 20000, 0, 20000, 0, 0]
    assert {x.why for x in r.ending} == {"notice", "closed"}


def test_a_lease_past_its_end_date_counts_as_staying(owner, prop):
    lease(owner, prop, "C1", end=TODAY - datetime.timedelta(days=3))
    r = report(owner)
    assert r.holding_over == 1 and r.holding_over_rent == 20000 and r.ending == []
    assert all(m.contracted == 20000 for m in r.months)


def test_a_recorded_rent_change_and_a_future_lease_count(owner, prop):
    old = lease(owner, prop, "D1")
    LeaseRentChange.objects.create(organization=owner.organization, lease=old, effective_from=D(2026, 5, 1),
                                   amount=Decimal("25000"))
    lease(owner, prop, "D2", start=D(2026, 6, 1))
    r = report(owner)
    assert [m.contracted for m in r.months] == [20000, 20000, 25000, 45000, 45000, 45000]
    assert [m.starting for m in r.months] == [0, 0, 0, 20000, 0, 0]


def test_drafts_and_other_organizations_are_left_out(owner, prop):
    other = make_org(name="Other")
    lease(other, make_property(other.organization), "Z1")
    lease(owner, prop, "E1", status="DRAFT")
    assert report(owner).contracted_total == 0


def test_empty_units_with_and_without_an_asking_rent(owner, prop):
    from properties import services as property_services
    a = property_services.create_unit(owner, prop, code="V1")
    property_services.create_unit(owner, prop, code="V2")
    type(a).objects.filter(pk=a.pk).update(list_rent=Decimal("15000"))
    r = report(owner)
    assert (r.empty_units, r.empty_priced, r.empty_rent) == (2, 1, 15000)


def test_rights(owner, prop):
    lease(owner, prop, "F1", end=D(2026, 5, 31))
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    with pytest.raises(PermissionDenied):
        report(caretaker)


def test_page_and_csv(client, owner, prop):
    lease(owner, prop, "G1", end=datetime.date.today() + datetime.timedelta(days=40))
    login(client, owner)
    page = client.get(reverse("reports:outlook"))
    assert page.status_code == 200 and b"Rent outlook" in page.content
    body = client.get(reverse("reports:outlook"), {"format": "csv"}).content.decode("utf-8-sig")
    assert "Contracted rent" in body and "Tenant G1" in body
