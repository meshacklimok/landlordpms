"""True property profitability (D-191)."""

import datetime
from decimal import Decimal

import pytest
from django.urls import reverse

from accounts.tests.factories import make_org, make_property
from accounts.tests.test_isolation import login
from expenses import services as expense_services
from expenses.models import CostClass, ExpenseCategory
from properties import services as property_services
from reports import profitability
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date
TODAY = D(2025, 4, 20)
START, END = D(2025, 1, 1), D(2025, 3, 31)


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


def spend(owner, prop, class_, amount, day=D(2025, 2, 10), name=None):  # noqa: B008 - a fixed date, built once
    category = ExpenseCategory.objects.create(organization=owner.organization, name=name or str(class_.label),
                                              cost_class=class_)
    return expense_services.record_expense(owner, prop, category=category, description="x", amount=amount,
                                           paid_on=day, method="CASH", today=TODAY)


def test_defaults_carry_a_cost_class(owner):
    expense_services.ensure_categories(owner.organization)
    got = {c.name: c.cost_class for c in ExpenseCategory.objects.for_org(owner.organization)}
    assert got["Caretaker and staff wages"] == CostClass.SALARIES
    assert got["Security"] == CostClass.SECURITY and got["Insurance"] == CostClass.INSURANCE
    assert got["Cleaning and garbage"] == CostClass.GARBAGE and got["Water"] == CostClass.UTILITIES
    assert got["Other"] == CostClass.OTHER


def test_costs_are_grouped_by_kind_and_taken_off_income(owner, prop):
    lease = make_lease(owner, prop, code="A1")
    bill(lease, D(2025, 1, 1), D(2025, 2, 1))
    pay(owner, lease, 20000, D(2025, 1, 6))
    spend(owner, prop, CostClass.SALARIES, 5000)
    spend(owner, prop, CostClass.SECURITY, 3000)
    spend(owner, prop, CostClass.SALARIES, 1000, name="Gardener")
    r = profitability.profitability(owner, START, END, today=TODAY)
    (row,) = r.rows
    assert row.costs[CostClass.SALARIES] == money(6000) and row.costs[CostClass.SECURITY] == money(3000)
    assert row.income == money(20000) and row.operating_costs == money(9000) and row.noi == money(11000)
    assert row.billed == money(40000) and row.collected == money(20000) and row.uncollected == money(20000)
    assert r.total.noi == row.noi and r.focus.noi == row.noi


def test_management_fee_is_a_cost_and_owed_is_today(owner, prop):
    prop = property_services.update_property(owner, prop, management_fee_percent=Decimal("10"))
    lease = make_lease(owner, prop, code="A1")
    bill(lease, D(2025, 1, 1), D(2025, 2, 1))
    pay(owner, lease, 20000, D(2025, 1, 6))
    r = profitability.profitability(owner, START, END, today=TODAY)
    (row,) = r.rows
    assert row.fee == money(2000) and row.noi == money(18000)
    assert row.costs_with_fee[CostClass.MANAGEMENT] == money(2000) and r.fees_counted
    assert row.owed == money(20000)


def test_page_and_csv_load(client, owner, prop):
    login(client, owner)
    lease = make_lease(owner, prop, code="A1")
    bill(lease, D(2025, 1, 1))
    pay(owner, lease, 20000, D(2025, 1, 6))
    spend(owner, prop, CostClass.INSURANCE, 2500)
    url = reverse("reports:profitability")
    page = client.get(url, {"start": "2025-01", "end": "2025-03"})
    assert page.status_code == 200 and b"True profitability" in page.content
    csv = client.get(url, {"start": "2025-01", "end": "2025-03", "format": "csv"})
    assert csv.status_code == 200 and b"Net operating income" in csv.content
