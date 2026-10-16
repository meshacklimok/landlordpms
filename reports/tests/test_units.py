"""Unit by unit (D-112), checked against hand-worked figures."""

import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from accounts.tests.factories import add_member, make_org, make_property
from leases.models import Lease
from properties import services as property_services
from properties.models import Unit
from reports import metrics, units
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date
JAN, FEB, MAR = D(2025, 1, 1), D(2025, 2, 1), D(2025, 3, 1)
TODAY = D(2025, 3, 20)


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


def came_in(unit, day):
    Unit.objects.filter(pk=unit.pk).update(created_at=timezone.make_aware(datetime.datetime.combine(day,
                                                                                                    datetime.time())))


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


@pytest.fixture
def block(owner, prop):
    """A1 pays Jan in time, Feb late and not Mar; A2 part pays Jan; V1 has stood empty since 1 Feb."""
    good = make_lease(owner, prop, code="A1")
    part = make_lease(owner, prop, code="A2", rent=30000)
    bill(good, JAN, FEB, MAR)
    bill(part, JAN)
    pay(owner, good, 20000, D(2025, 1, 6))
    pay(owner, good, 20000, D(2025, 2, 20))
    pay(owner, part, 10000, D(2025, 1, 25))
    empty = property_services.create_unit(owner, prop, code="V1", list_rent=12000)
    came_in(empty, FEB)
    return good, part, empty


def row(report, code):
    return next(r for r in report.rows if r.unit.code == code)


def test_each_month_says_whether_it_was_paid_in_time(owner, block):
    report = units.unit_report(owner, today=TODAY)
    assert report.months[0] == D(2024, 4, 1) and report.this_month == MAR
    a1 = row(report, "A1")
    assert [c.state for c in a1.cells[-3:]] == [units.PAID, units.LATE, units.UNPAID]
    assert all(c.state == units.NONE for c in a1.cells[:-3])
    assert (a1.expected, a1.collected, a1.months_paid_in_time, a1.months_billed) == (money(60000), money(40000), 1, 3)
    a2 = row(report, "A2")
    assert a2.cells[-3].state == units.PART and a2.cells[-3].collected == money(10000)


def test_a_unit_adds_up_to_the_dashboard(owner, block):
    report = units.unit_report(owner, today=TODAY)
    trend = metrics.trend(metrics.scope(owner), MAR)
    assert report.summary.expected == sum((m.expected for m in trend), Decimal(0))
    assert report.summary.collected == sum((m.collected for m in trend), Decimal(0))


def test_rent_asking_rent_and_empty_days(owner, block):
    good, _part, _empty = block
    Unit.objects.filter(pk=good.unit_id).update(list_rent=25000)
    report = units.unit_report(owner, today=TODAY)
    a1 = row(report, "A1")
    assert (a1.status, a1.rent, a1.rent_since, a1.below_asking) == ("occupied", money(20000), JAN, money(5000))
    v1 = row(report, "V1")
    assert v1.status == "vacant" and v1.lease is None
    assert (v1.empty_days, v1.days_in_window) == (48, 48)  # 1 Feb to 20 Mar
    assert v1.empty_loss == money(Decimal(12000) * 12 * 48 / 365)
    assert a1.empty_days == 0


def test_a_unit_empty_between_tenants(owner, prop):
    lease = make_lease(owner, prop, start=JAN)
    Lease.all_objects.filter(pk=lease.pk).update(status=Lease.Status.ENDED, ended_on=D(2025, 1, 31))
    report = units.unit_report(owner, today=TODAY)
    assert row(report, "A1").empty_days == 48


def test_the_summary(owner, block):
    good, part, _empty = block
    Lease.all_objects.filter(pk=part.pk).update(end_date=D(2025, 5, 31))
    report = units.unit_report(owner, today=TODAY)
    s = report.summary
    assert (s.units, s.occupied, s.empty_now) == (3, 2, 1)
    assert (s.billed, s.paid, s.unpaid) == (1, 0, 1)  # only A1 is billed in March
    assert s.always_in_time == 0
    assert s.average_rent == money(25000)
    assert [r.unit.code for r in s.ending] == ["A2"]
    a1 = row(report, "A1")
    assert a1.overdue == money(20000) and a1.days_late > 0


def test_by_kind_of_unit(owner, block):
    good, _part, empty = block
    Unit.objects.filter(pk__in=[good.unit_id, empty.pk]).update(type_label="2 bedroom")
    report = units.unit_report(owner, today=TODAY)
    kinds = {t.label: t for t in report.by_type}
    assert set(kinds) == {"2 bedroom", "Apartment"}
    two = kinds["2 bedroom"]
    assert (two.units, two.occupied, two.average_rent, two.average_asking) == (2, 1, money(20000), money(12000))


def test_when_in_the_month_tenants_pay(owner, block):
    report = units.unit_report(owner, today=TODAY)
    days = {p.label: p for p in report.pay_days}
    assert days["6–10"].count == 1 and days["16–20"].count == 1 and days["21–25"].amount == money(10000)
    assert days["6–10"].share == Decimal(20000) / Decimal(50000)


def test_a_scoped_member_and_names(owner, block, prop):
    other = make_property(owner.organization, name="Baobab Flats")
    make_lease(owner, other, code="B1")
    report = units.unit_report(owner, property=other, today=TODAY)
    assert [r.unit.code for r in report.rows] == ["B1"]
    accountant = add_member(owner.organization, "accountant", all_properties=True)
    assert len(units.unit_report(accountant, today=TODAY).rows) == 4


def test_the_page_and_csv(client, owner, block):
    client.force_login(owner.user)
    page = client.get("/reports/units/").content.decode()
    assert "Unit by unit" in page and "V1" in page and "Tenant A1" in page and "When in the month" in page
    assert "Unit by unit" in client.get("/reports/").content.decode()
    response = client.get("/reports/units/?format=csv")
    body = response.content.decode("utf-8-sig")
    assert response["Content-Disposition"].startswith('attachment; filename="unit-by-unit-')
    assert "Acacia Court,V1," in body and "By kind of unit" in body


def test_a_big_portfolio_asks_for_a_property(client, owner, prop, monkeypatch):
    monkeypatch.setattr(units, "UNIT_LIMIT", 1)
    make_lease(owner, prop, code="A1")
    make_lease(owner, prop, code="A2")
    client.force_login(owner.user)
    page = client.get("/reports/units/").content.decode()
    assert "Choose a property above" in page and "unit-months" not in page
    page = client.get(f"/reports/units/?property={prop.public_id}").content.decode()
    assert "unit-months" in page


def test_the_page_is_refused_without_financial_figures(client, owner):
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    client.force_login(caretaker.user)
    assert client.get("/reports/units/").status_code == 403
