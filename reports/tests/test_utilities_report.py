"""The water and electricity report (D-132): usage, billed, collected, baselines, rights, isolation."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from meters import services
from meters.tests.conftest import jpeg, let, make_meter, read
from reports import operations

pytestmark = pytest.mark.django_db

D = datetime.date
START, END = D(2026, 1, 1), D(2026, 1, 31)


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


def metered(owner, prop, code="A1", *, utility="WATER", rate="100", label=None, value=100, end_value=140):
    """A let unit on its own meter: baseline on 31 Dec, a reading on 31 Jan, both approved."""
    lease, unit = let(owner, prop, code=code)
    meter = make_meter(owner, prop, [unit], rate=rate, utility=utility, label=label or code)
    services.approve(owner, read(owner, meter, D(2025, 12, 31), value), today=D(2025, 12, 31))
    reading = read(owner, meter, D(2026, 1, 31), end_value, photo=jpeg())
    services.approve(owner, reading, today=D(2026, 1, 31))
    return lease, meter


def report(owner, **kw):
    return operations.utilities(owner, START, END, **kw)


def test_nothing(owner):
    r = report(owner)
    assert r.by_utility == [] and r.billed == 0 and r.usage == {}


def test_usage_and_billed(owner, prop):
    metered(owner, prop, "A1")
    metered(owner, prop, "A2", utility="ELECTRICITY", rate="20", end_value=150)
    r = report(owner)
    assert r.usage == {"WATER": Decimal(40), "ELECTRICITY": Decimal(50)}
    assert [(x.name, x.usage) for x in r.by_utility] == [("Water", 40), ("Electricity", 50)]
    assert r.billed == Decimal(5000) and r.invoiced == 0 and r.uninvoiced == 5000
    assert [x.name for x in r.by_meter][0].endswith("A2")


def test_the_baseline_adds_no_usage_and_other_periods_are_left_out(owner, prop):
    metered(owner, prop, "A1")
    r = operations.utilities(owner, D(2025, 12, 1), D(2025, 12, 31))
    assert r.usage == {} and r.billed == 0


def test_other_organizations_and_property_filter(owner, prop):
    other = make_org(name="Other")
    metered(other, make_property(other.organization), "Z1")
    metered(owner, prop, "A1")
    second = make_property(owner.organization, name="Second")
    metered(owner, second, "B1")
    assert report(owner).usage["WATER"] == 80
    assert report(owner, prop=prop).usage["WATER"] == 40


def test_money_needs_the_financial_right(owner, prop):
    metered(owner, prop, "A1")
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    r = report(caretaker)
    assert r.usage["WATER"] == 40 and not r.show_money and r.billed == 0


def test_needs_the_meters_right(owner, prop):
    leasing = add_member(owner.organization, "leasing_agent", properties=[prop])
    try:
        report(leasing)
    except PermissionDenied:
        pass


def test_page_and_csv(client, owner, prop):
    metered(owner, prop, "A1")
    login(client, owner)
    page = client.get(reverse("reports:utilities"), {"period": "custom", "from": "2026-01-01", "to": "2026-01-31"})
    assert page.status_code == 200 and "Water" in page.content.decode()
    body = client.get(reverse("reports:utilities"), {"format": "csv", "period": "custom", "from": "2026-01-01",
                                                      "to": "2026-01-31"}).content.decode("utf-8-sig")
    assert "Biggest users" in body
    assert "Water and electricity" in client.get(reverse("reports:centre")).content.decode()


def test_invoiced_and_collected_share(owner, prop):
    from billing.tests.test_invoicing import bill
    from reports.tests.test_income import pay

    lease, _meter = metered(owner, prop, "A1")
    bill(lease, D(2026, 2, 1))  # rent 20,000 (billing fixture) plus the 4,000 of water
    r = report(owner)
    assert r.billed == Decimal(4000) and r.invoiced == Decimal(4000)
    pay(owner, lease, 6000, D(2026, 2, 10))
    r = report(owner)
    assert 0 < r.collected < r.invoiced
