"""Profit and loss, cash flow and aged receivables by property (D-065)."""

import datetime
from decimal import Decimal

import pytest
from django.urls import reverse

from accounts.tests.factories import add_member, make_org, make_property
from accounts.tests.test_isolation import login
from billing import deposits
from billing.tests.test_invoicing import FEB, JAN, bill, make_lease
from payments import services as payment_services
from payments.models import Payment
from properties import services as property_services
from reports import finance
from reports.models import OwnerRemittance

pytestmark = pytest.mark.django_db

D = datetime.date
MAR_20 = D(2026, 3, 20)


def money(value) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def pay(owner, lease, amount, day):
    return payment_services.record_payment(owner, lease, amount=amount, method=Payment.Method.CASH, paid_at=day)


@pytest.fixture
def owner():
    return make_org(name="Acme")


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def acacia(org):
    return make_property(org, name="Acacia")


@pytest.fixture
def baobab(org):
    return make_property(org, name="Baobab")


@pytest.fixture
def books(owner, org, acacia, baobab):
    """Acacia: 15,000 a month, paid in full and 5,000 ahead; a 3,000 deposit refund in February.
    Baobab: 10,000 a month, nothing paid. 8,000 paid to an owner in February."""
    a1 = make_lease(owner, acacia, code="A1")
    bill(a1, JAN)
    bill(a1, FEB)
    pay(owner, a1, 15000, D(2026, 1, 10))
    pay(owner, a1, 20000, D(2026, 2, 8))
    deposits.record_received(owner, a1, amount=15000, entry_date=JAN)
    deposits.refund(owner, a1, amount=3000, entry_date=D(2026, 2, 20))
    b1 = make_lease(owner, baobab, code="B1", rent=10000)
    bill(b1, JAN)
    bill(b1, FEB)
    jane = property_services.create_owner(owner, name="Jane Wanjiru", phone="+254712000111")
    OwnerRemittance.objects.create(organization=org, owner=jane, month=JAN, amount=8000, paid_on=D(2026, 2, 25))
    OwnerRemittance.objects.create(organization=org, owner=jane, month=JAN, amount=999, paid_on=D(2026, 2, 26),
                                   voided_at=datetime.datetime(2026, 2, 27, tzinfo=datetime.UTC))
    return a1, b1


# ---------------------------------------------------------------------------
# The period
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("start", "end", "expected"), [
    ("", "", (D(2026, 4, 1), D(2026, 9, 1))),
    ("2026-01", "2026-03", (D(2026, 1, 1), D(2026, 3, 1))),
    ("2026-03", "2026-01", (D(2026, 1, 1), D(2026, 3, 1))),
    ("2024-01", "2026-03", (D(2025, 4, 1), D(2026, 3, 1))),
    ("junk", "2026-03", (D(2025, 10, 1), D(2026, 3, 1))),
])
def test_the_period(start, end, expected):
    assert finance.period(start, end, D(2026, 9, 29)) == expected


# ---------------------------------------------------------------------------
# Profit and loss, cash flow
# ---------------------------------------------------------------------------


def test_profit_and_loss_is_cash_basis_with_rent_billed_as_a_memo(owner, books):
    report = finance.money_report(owner, JAN, FEB)
    jan, feb = report.months
    assert (jan.rent, jan.unapplied, jan.rent_billed) == (money(15000), 0, money(25000))
    assert (feb.rent, feb.unapplied, feb.rent_billed) == (money(15000), money(5000), money(25000))
    assert report.total.income == report.total.noi == money(35000)
    assert jan.collection_rate == Decimal("0.6")
    assert report.label == "Jan 2026 – Feb 2026"


def test_cash_flow_counts_refunds_and_owner_payments_for_the_whole_organization(owner, books):
    report = finance.money_report(owner, JAN, FEB)
    assert report.whole_organization
    feb = report.months[1]
    assert (feb.money_in, feb.refunds, feb.owner_payments) == (money(20000), money(3000), money(8000))
    assert feb.net == money(9000)
    assert report.total.net == money(24000)


def test_one_property_leaves_out_owner_payments(owner, books, acacia, baobab):
    report = finance.money_report(owner, JAN, FEB, acacia)
    assert not report.whole_organization
    assert report.total.owner_payments == 0
    assert report.total.refunds == money(3000)
    assert report.total.rent_billed == money(30000)
    assert any("left out" in note for note in report.notes)
    other = finance.money_report(owner, JAN, FEB, baobab)
    assert (other.total.income, other.total.refunds, other.total.rent_billed) == (0, 0, money(20000))


def test_a_scoped_member_sees_only_their_properties(org, books, baobab):
    member = add_member(org, "accountant", properties=[baobab])
    report = finance.money_report(member, JAN, FEB)
    assert not report.whole_organization
    assert (report.total.income, report.total.rent_billed, report.total.owner_payments) == (0, money(20000), 0)


def test_a_reversed_payment_and_a_reversed_refund_are_left_out(owner, books):
    a1, _b1 = books
    payment_services.reverse_payment(owner, Payment.objects.get(amount=20000), reason="Bounced")
    refund = a1.deposit_entries.get(kind="DEPOSIT_REFUNDED")
    deposits.reverse(owner, refund, reason="Typed twice")
    report = finance.money_report(owner, FEB, FEB)
    assert (report.total.income, report.total.refunds) == (0, 0)


# ---------------------------------------------------------------------------
# Aged receivables
# ---------------------------------------------------------------------------


def test_receivables_by_property_leave_out_credit(owner, books, baobab):
    rows, total = finance.receivables(owner, MAR_20)
    assert [row.property for row in rows] == [baobab]
    assert rows[0].total == total.total == money(20000)
    assert [r.lease for r in rows[0].leases] == [books[1]]
    assert sum(total.buckets.values()) == money(20000)
    assert total.buckets["current"] == 0


def test_receivables_for_one_property(owner, books, acacia):
    assert finance.receivables(owner, MAR_20, acacia)[0] == []


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["profit_loss", "cash_flow", "receivables"])
def test_the_pages_and_csv(client, owner, books, acacia, name):
    login(client, owner)
    url = reverse(f"reports:{name}")
    assert client.get(url, {"from": "2026-01", "to": "2026-02"}).status_code == 200
    assert client.get(url, {"property": str(acacia.public_id)}).status_code == 200
    response = client.get(url, {"from": "2026-01", "to": "2026-02", "format": "csv"})
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert b"Total" in response.content
    assert client.get(url, {"property": "00000000-0000-0000-0000-000000000000"}).status_code == 404


def test_the_profit_and_loss_csv_has_the_figures(client, owner, books):
    login(client, owner)
    content = client.get(reverse("reports:profit_loss"), {"from": "2026-01", "to": "2026-02",
                                                          "format": "csv"}).content.decode()
    assert "2026-02,15000.00,0.00,5000.00,20000.00,0.00,20000.00,25000.00,80.0,0.00" in content


def test_the_pages_need_financial_reports(client, org, acacia):
    login(client, add_member(org, "caretaker", properties=[acacia]))
    for name in ("profit_loss", "cash_flow", "receivables"):
        assert client.get(reverse(f"reports:{name}")).status_code == 403


def test_another_organizations_property_is_not_found(client, owner, books):
    stranger_prop = make_property(make_org(name="Other").organization)
    login(client, owner)
    assert client.get(reverse("reports:cash_flow"), {"property": str(stranger_prop.public_id)}).status_code == 404


# ---------------------------------------------------------------------------
# Comparisons and breakdowns (D-110)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("start", "end", "expected"), [
    (D(2026, 1, 1), D(2026, 2, 1), (D(2025, 11, 1), D(2025, 12, 1))),
    (D(2026, 3, 1), D(2026, 3, 1), (D(2026, 2, 1), D(2026, 2, 1))),
    (D(2025, 4, 1), D(2026, 3, 1), (D(2024, 4, 1), D(2025, 3, 1))),
])
def test_the_previous_period_is_as_long_and_just_before(start, end, expected):
    assert finance.previous_period(start, end) == expected


def test_by_property_splits_the_figures(owner, books, acacia, baobab):
    report = finance.money_report(owner, JAN, FEB)
    assert [p.property for p in report.by_property] == [acacia, baobab]
    a, b = (p.figures for p in report.by_property)
    assert (a.income, a.rent_billed, a.refunds, a.owner_payments) == (money(35000), money(30000), money(3000), 0)
    assert (b.income, b.rent_billed, b.collection_rate) == (0, money(20000), Decimal(0))
    assert a.income + b.income == report.total.income
    # One property in view: nothing to split.
    assert finance.money_report(owner, JAN, FEB, acacia).by_property == []


def test_margin_average_and_the_previous_period(owner, books):
    report = finance.money_report(owner, JAN, FEB, compare=True)
    assert (report.total.margin, report.total.expense_ratio) == (Decimal(1), Decimal(0))
    assert report.average.income == money(17500)
    assert report.previous.label == "Nov 2025 – Dec 2025"
    change = report.change("income")
    assert (change.now, change.before, change.ratio) == (money(35000), 0, None)
    assert finance.money_report(owner, JAN, FEB).previous is None
    assert finance.money_report(owner, JAN, FEB).change("income").delta is None


def test_receivables_summary(owner, books):
    rows, total = finance.receivables(owner, MAR_20)
    summary = finance.receivables_summary(rows, total)
    assert summary.leases == 1 and summary.overdue == money(20000)
    assert [r.lease for r in summary.largest] == [books[1]]
    assert sum(share for _k, _l, _a, share in summary.shares) == 1
    assert summary.over_60_share is not None


def test_the_pages_show_the_analysis(client, owner, books):
    login(client, owner)
    page = client.get(reverse("reports:profit_loss"), {"from": "2026-01", "to": "2026-02"}).content.decode()
    assert "By property" in page and "Nov 2025 – Dec 2025" in page and 'id="chart-pl-months"' in page
    page = client.get(reverse("reports:cash_flow"), {"from": "2026-01", "to": "2026-02"}).content.decode()
    assert "By property" in page and "Where money in came from" in page and 'data-chart="chart-cash-months"' in page
    page = client.get(reverse("reports:receivables")).content.decode()
    assert "Largest balances" in page and "Share of total" in page and 'id="chart-aging"' in page
