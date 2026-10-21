"""Tenant statement, expenses report, payment statement and month on month."""

import datetime
from decimal import Decimal

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from expenses.models import Expense, ExpenseCategory
from reports import expense_report, operations
from reports.tests.test_income import bill, make_lease, pay

pytestmark = pytest.mark.django_db

D = datetime.date
Q1 = {"period": "custom", "from": "2025-01-01", "to": "2025-03-31"}


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def prop(owner):
    return make_property(owner.organization, name="Acacia Court")


def spend(owner, prop, amount, day, category, status="APPROVED", n=[0]):  # noqa: B006 - the counter is meant to be shared between calls
    n[0] += 1
    return Expense.objects.create(organization=owner.organization, property=prop, category=category,
                                  description="Work", amount=amount, paid_on=day, status=status,
                                  recorded_by=owner.user, number=f"E{n[0]}", reference_key=f"k{n[0]}")


def test_tenant_statement_lists_charges_payments_and_balance(client, owner, prop):
    lease = make_lease(owner, prop, code="S1")
    bill(lease, D(2025, 1, 1), D(2025, 2, 1))
    pay(owner, lease, 20000, D(2025, 1, 10))
    login(client, owner)
    page = client.get(reverse("reports:tenant_statement"), {"lease": lease.public_id, **Q1})
    assert page.status_code == 200
    st = page.context["statement"]
    # January's invoice was issued in December, so it is brought forward; the payment and February's invoice follow.
    assert st["brought_forward"] == Decimal("20000") and st["closing"] == Decimal("20000")
    assert len(st["rows"]) == 2
    assert "Tenant S1" in page.content.decode()
    csv = client.get(reverse("reports:tenant_statement"), {"lease": lease.public_id, **Q1, "format": "csv"})
    text = csv.content.decode("utf-8-sig")
    assert "Closing balance" in text and "Tenant S1" in text


def test_tenant_statement_picker_and_other_organization(client, owner, prop):
    lease = make_lease(owner, prop, code="S1")
    login(client, owner)
    assert lease in client.get(reverse("reports:tenant_statement")).context["choices"]
    other = make_org(name="Other")
    foreign = make_lease(other, make_property(other.organization), code="Z1")
    assert client.get(reverse("reports:tenant_statement"), {"lease": foreign.public_id}).status_code == 404


def test_tenant_statement_needs_invoice_rights(client, owner, prop):
    lease = make_lease(owner, prop, code="S1")
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    login(client, caretaker)
    assert client.get(reverse("reports:tenant_statement"), {"lease": lease.public_id}).status_code == 403


def test_expenses_report_groups_approved_only(owner, prop):
    repairs = ExpenseCategory.objects.create(organization=owner.organization, name="Repairs")
    security = ExpenseCategory.objects.create(organization=owner.organization, name="Security")
    spend(owner, prop, 1000, D(2025, 1, 5), repairs)
    spend(owner, prop, 3000, D(2025, 2, 5), security)
    spend(owner, prop, 500, D(2025, 2, 6), repairs, status="SUBMITTED")
    spend(owner, prop, 700, D(2025, 2, 7), repairs, status="VOIDED")
    r = expense_report.expenses(owner, D(2025, 1, 1), D(2025, 3, 31))
    assert (r.total, r.count, r.waiting, r.waiting_amount) == (4000, 2, 1, 500)
    assert [(label, a) for label, _n, a, _s in r.by_category] == [("Security", 3000), ("Repairs", 1000)]
    assert r.by_category[0][3] == Decimal("0.75")
    assert [label for _m, label, _n, _a in r.months] == ["Jan 2025", "Feb 2025"]


def test_expenses_report_page_csv_and_rights(client, owner, prop):
    repairs = ExpenseCategory.objects.create(organization=owner.organization, name="Repairs")
    spend(owner, prop, 1000, D(2025, 1, 5), repairs)
    login(client, owner)
    page = client.get(reverse("reports:expenses"), Q1)
    assert page.status_code == 200 and "Repairs" in page.content.decode()
    csv = client.get(reverse("reports:expenses"), {**Q1, "format": "csv"})
    assert "Every approved expense" in csv.content.decode("utf-8-sig")
    caretaker = add_member(owner.organization, "caretaker", properties=[prop])
    login(client, caretaker)
    assert client.get(reverse("reports:expenses"), Q1).status_code == 403


def test_payment_statement_lists_every_payment(client, owner, prop):
    lease = make_lease(owner, prop, code="P1")
    pay(owner, lease, 5000, D(2025, 1, 10))
    pay(owner, lease, 7000, D(2025, 2, 10))
    r = operations.payments(owner, D(2025, 1, 1), D(2025, 3, 31))
    assert [p.amount for p in r.lines] == [7000, 5000] and r.lines_total == 2
    login(client, owner)
    page = client.get(reverse("reports:payments"), Q1)
    assert "Payment statement" in page.content.decode()
    csv = client.get(reverse("reports:payments"), {**Q1, "format": "csv"}).content.decode("utf-8-sig")
    assert "Every payment in the period" in csv and "Tenant P1" in csv


def test_month_on_month_compares_each_month_to_the_one_before(client, owner, prop):
    repairs = ExpenseCategory.objects.create(organization=owner.organization, name="Repairs")
    lease = make_lease(owner, prop, code="M1")
    bill(lease, D(2025, 1, 1), D(2025, 2, 1))
    pay(owner, lease, 10000, D(2025, 1, 10))
    pay(owner, lease, 15000, D(2025, 2, 10))
    spend(owner, prop, 1000, D(2025, 2, 5), repairs)
    login(client, owner)
    page = client.get(reverse("reports:month_on_month"), {"from": "2025-01", "to": "2025-02"})
    assert page.status_code == 200
    (jan, jan_ch), (feb, feb_ch) = page.context["rows"]
    assert (jan.income, feb.income) == (10000, 15000)
    assert feb_ch["income"].delta == 5000 and feb_ch["income"].ratio == Decimal("0.5")
    assert jan_ch["income"].before == 0  # December had nothing
    csv = client.get(reverse("reports:month_on_month"), {"from": "2025-01", "to": "2025-02", "format": "csv"})
    assert "Change in income %" in csv.content.decode("utf-8-sig")


def test_the_new_reports_are_in_the_centre(client, owner, prop):
    login(client, owner)
    page = client.get(reverse("reports:centre")).content.decode()
    for label in ("Tenant statement", "Expenses report", "Month on month"):
        assert label in page


def test_tenant_statement_pdf_and_summary(client, owner, prop):
    lease = make_lease(owner, prop, code="S1")
    bill(lease, D(2025, 1, 1), D(2025, 2, 1))
    pay(owner, lease, 20000, D(2025, 1, 10))
    login(client, owner)
    page = client.get(reverse("reports:tenant_statement"), {"lease": lease.public_id, **Q1})
    assert "aging" in page.context and "Download PDF" in page.content.decode()
    pdf = client.get(reverse("reports:tenant_statement"), {"lease": lease.public_id, **Q1, "format": "pdf"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_expenses_and_month_on_month_say_what_stands_out(client, owner, prop):
    repairs = ExpenseCategory.objects.create(organization=owner.organization, name="Repairs")
    security = ExpenseCategory.objects.create(organization=owner.organization, name="Security")
    spend(owner, prop, 1000, D(2025, 1, 5), repairs)
    spend(owner, prop, 3000, D(2025, 2, 5), security)
    login(client, owner)
    page = client.get(reverse("reports:expenses"), Q1).content.decode()
    assert "Security was the largest cost" in page and "largest single expense" in page
    mom = client.get(reverse("reports:month_on_month"), {"from": "2025-01", "to": "2025-02"})
    assert mom.context["notes"]
