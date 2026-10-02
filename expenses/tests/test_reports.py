"""Expenses in the profit and loss, cash flow and owner statement (D-067 item 8)."""

import datetime
from decimal import Decimal

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_property
from accounts.tests.test_isolation import login
from properties import services as property_services
from reports import finance, owners, statements
from reports.tests.test_income import bill, make_lease, pay

from .conftest import spend

pytestmark = pytest.mark.django_db

D = datetime.date
JAN, FEB = D(2025, 1, 1), D(2025, 2, 1)


def money(value):
    return Decimal(value).quantize(Decimal("0.01"))


@pytest.fixture
def jane(owner):
    return property_services.create_owner(owner, name="Jane Wanjiru", phone="+254712000111",
                                          email="jane@example.com")


@pytest.fixture
def books(owner, org, prop, jane, repairs):
    """January: 20,000 rent collected, 10% fee, 3,000 of approved expenses, 500 waiting, 700 voided."""
    property_services.update_property(owner, prop, owner=jane, management_fee_percent=Decimal("10"))
    lease = make_lease(owner, prop, code="A1", rent=20000)
    bill(lease, JAN)
    pay(owner, lease, 20000, D(2025, 1, 10))
    security = repairs.__class__.objects.get(organization=org, name="Security")
    spend(owner, prop, repairs, "1000", paid_on=D(2025, 1, 5), description="Gate lock")
    spend(owner, prop, security, "2000", paid_on=D(2025, 1, 31), description="Guards")
    spend(add_member(org, "manager", all_properties=True), prop, repairs, "500", paid_on=D(2025, 1, 20))
    from expenses import services
    services.void(owner, spend(owner, prop, repairs, "700", paid_on=D(2025, 1, 8)), reason="Twice")
    spend(owner, prop, repairs, "400", paid_on=D(2025, 2, 1), description="February")
    return lease


def test_profit_and_loss_takes_off_approved_expenses(owner, books):
    report = finance.money_report(owner, JAN, FEB)
    jan, feb = report.months
    assert (jan.income, jan.expenses, jan.noi) == (money(20000), money(3000), money(17000))
    assert (feb.expenses, feb.noi) == (money(400), money(-400))
    assert report.total.expenses == money(3400)
    assert report.expense_categories == [("Security", money(2000)), ("Repairs and maintenance", money(1400))]
    assert any("1 expense of KES 500.00 is waiting" in n for n in report.notes)


def test_cash_flow_counts_expenses_as_money_out(owner, books):
    jan = finance.money_report(owner, JAN, JAN).months[0]
    assert jan.money_out == money(3000) and jan.net == money(17000)


def test_a_scoped_member_counts_only_their_properties(owner, org, books, repairs):
    other = make_property(org, name="Baobab Flats")
    spend(owner, other, repairs, "9000", paid_on=D(2025, 1, 3))
    assert finance.money_report(owner, JAN, JAN).total.expenses == money(12000)
    scoped = add_member(org, "accountant", properties=[other])
    assert finance.money_report(scoped, JAN, JAN).total.expenses == money(9000)


def test_the_pages_show_expenses(client, owner, books):
    login(client, owner)
    page = client.get(reverse("reports:profit_loss"), {"from": "2025-01", "to": "2025-01"}).content.decode()
    assert "Guards" not in page and "Security" in page and "3,000.00" in page and "17,000.00" in page
    csv = client.get(reverse("reports:cash_flow"), {"from": "2025-01", "to": "2025-01", "format": "csv"})
    assert "2025-01,20000.00,0.00,0.00,0.00,20000.00,3000.00,0.00" in csv.content.decode()


def test_the_owner_statement_takes_off_expenses(owner, jane, books):
    st = statements.statement(owner, str(jane.public_id), JAN)
    (block,) = st.blocks
    assert [e.description for e in block.expenses] == ["Gate lock", "Guards"]
    assert (st.collected, st.fee, st.expenses, st.due) == (money(20000), money(2000), money(3000), money(15000))
    assert any("waiting for approval" in n for n in st.notes)


def test_due_can_be_negative(owner, org, jane, prop, repairs):
    property_services.update_property(owner, prop, owner=jane)
    spend(owner, prop, repairs, "5000", paid_on=D(2025, 1, 5))
    st = statements.statement(owner, str(jane.public_id), JAN)
    assert st.due == money(-5000)
    assert any("negative" in n for n in st.notes)


def test_the_sent_statement_stores_expenses(owner, jane, books, mailoutbox):
    st = statements.statement(owner, str(jane.public_id), JAN)
    send = owners.send_statement(owner, st)
    assert (send.expenses, send.due) == (money(3000), money(15000))
    assert "Expenses: KES 3,000.00" in mailoutbox[0].body


def test_the_statement_page_and_pdf(client, owner, jane, books):
    login(client, owner)
    page = client.get(reverse("reports:owner_statement"), {"owner": str(jane.public_id), "month": "2025-01"})
    assert "Gate lock" in page.content.decode() and "15,000.00" in page.content.decode()
    from reports.pdf import render_statement
    assert render_statement(statements.statement(owner, str(jane.public_id), JAN)).startswith(b"%PDF")
