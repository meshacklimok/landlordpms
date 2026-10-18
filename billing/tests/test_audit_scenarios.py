"""Pre-launch audit, financial scenarios (doc 23): the same money must read the same on every screen."""

import datetime
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import make_org, make_property
from billing import invoicing
from billing import selectors as billing_selectors
from billing.models import Invoice
from leases import services as lease_services
from payments import services as payment_services
from reports import metrics

from .test_invoicing import FEB, JAN, MAR, bill, make_lease

pytestmark = pytest.mark.django_db

D = datetime.date
ISSUED, PARTLY, PAID, VOID = (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.PAID,
                              Invoice.Status.VOID)
LATE = D(2026, 6, 1)  # long after every invoice below fell due


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


def pay(owner, lease, amount, day):
    return payment_services.record_payment(owner, lease, amount=amount, method="CASH", paid_at=day)


def owed(owner, lease, today=LATE):
    [row] = [r for r in billing_selectors.arrears(owner, today) if r.lease.pk == lease.pk] or [None]
    return row.balance if row else Decimal("0")


# ---------------------------------------------------------------------------
# 1. A KES 15,000 invoice part-paid
# ---------------------------------------------------------------------------


def test_a_part_payment_leaves_the_rest_owing_until_it_is_paid(owner, prop):
    lease = make_lease(owner, prop, rent=15000, due_day=5)
    invoice = bill(lease, FEB)
    pay(owner, lease, 6000, D(2026, 2, 5))
    invoice.refresh_from_db()
    assert (invoice.status, invoice.amount_paid, (invoice.total - invoice.amount_paid)) == (PARTLY, Decimal("6000.00"), Decimal("9000.00"))
    assert invoicing.lease_balance(lease) == Decimal("9000.00") == owed(owner, lease)
    assert invoice.is_overdue(D(2026, 2, 9))
    pay(owner, lease, 9000, D(2026, 2, 20))
    invoice.refresh_from_db()
    assert (invoice.status, (invoice.total - invoice.amount_paid)) == (PAID, Decimal("0.00"))
    assert invoicing.lease_balance(lease) == 0 and owed(owner, lease) == 0


def test_a_part_payment_pays_the_oldest_invoice_first(owner, prop):
    lease = make_lease(owner, prop, rent=15000, due_day=5)
    jan, feb = bill(lease, JAN), bill(lease, FEB)
    pay(owner, lease, 20000, D(2026, 2, 10))
    jan.refresh_from_db()
    feb.refresh_from_db()
    assert (jan.status, feb.status, (feb.total - feb.amount_paid)) == (PAID, PARTLY, Decimal("10000.00"))
    assert invoicing.lease_balance(lease) == Decimal("10000.00")


def test_a_payment_beyond_the_invoice_is_kept_as_credit_and_applied_to_the_next(owner, prop):
    lease = make_lease(owner, prop, rent=15000, due_day=5)
    feb = bill(lease, FEB)
    pay(owner, lease, 20000, D(2026, 2, 5))
    feb.refresh_from_db()
    assert feb.status == PAID
    assert payment_services.unallocated_credit(lease) == Decimal("5000.00")
    assert invoicing.lease_balance(lease) == Decimal("-5000.00")
    bill(lease, MAR)
    assert invoicing.lease_balance(lease) == Decimal("10000.00")


def test_a_reversed_payment_reopens_the_invoice(owner, prop):
    lease = make_lease(owner, prop, rent=15000)
    invoice = bill(lease, FEB)
    payment = pay(owner, lease, 15000, D(2026, 2, 3))
    payment_services.reverse_payment(owner, payment, reason="Bounced")
    invoice.refresh_from_db()
    assert invoice.status == ISSUED and (invoice.total - invoice.amount_paid) == Decimal("15000.00")
    assert invoicing.lease_balance(lease) == Decimal("15000.00")


# ---------------------------------------------------------------------------
# 2. Arrears: the lease account, the arrears page, the dashboard and the report agree
# ---------------------------------------------------------------------------


def test_arrears_agree_on_every_screen(client, owner, prop):
    a = make_lease(owner, prop, code="A1", rent=15000, due_day=5)
    b = make_lease(owner, prop, code="B2", rent=22000, due_day=5)
    c = make_lease(owner, prop, code="C3", rent=9000, due_day=5)
    for lease in (a, b, c):
        bill(lease, JAN)
        bill(lease, FEB)
    pay(owner, a, 15000, D(2026, 1, 5))  # A1: January paid, February owing
    pay(owner, b, 7000, D(2026, 1, 6))  # B2: part of January paid
    # C3 paid nothing.
    expected = {a: Decimal("15000.00"), b: Decimal("37000.00"), c: Decimal("18000.00")}
    for lease, amount in expected.items():
        assert invoicing.lease_balance(lease) == amount, lease.number
    total = sum(expected.values())  # 70,000.00

    today = timezone.localdate()
    rows = billing_selectors.arrears(owner, today)
    assert {r.lease.pk: r.balance for r in rows} == {lease.pk: amount for lease, amount in expected.items()}
    assert sum(r.balance for r in rows) == total
    assert billing_selectors.aging_totals(rows)["total"] == total
    assert billing_selectors.arrears_summary(rows, today).total == total

    # The dashboard's aging buckets add up to the same figure.
    assert metrics.arrears(metrics.scope(owner), today).total == total
    # Rent billed less rent collected, summed over the months, is the same debt (no water or other charges here).
    figures = metrics.months(metrics.scope(owner), JAN, FEB)
    assert sum(f.expected for f in figures) - sum(f.collected for f in figures) == total

    client.force_login(owner.user)
    page = client.get(reverse("billing:arrears"), {"all": "1"})
    assert page.context["totals"]["total"] == total
    assert page.context["summary"].total == total
    assert client.get("/reports/").status_code == 200
    csv = client.get("/reports/dashboard.csv").content.decode("utf-8-sig")
    assert "70000.00" in csv


# ---------------------------------------------------------------------------
# 3. Moving out in the middle of a month
# ---------------------------------------------------------------------------


def test_a_mid_month_move_out_bills_the_days_lived_and_nothing_after(owner, prop):
    lease = make_lease(owner, prop, rent=15000, due_day=5)
    jan, feb, mar = bill(lease, JAN), bill(lease, FEB), bill(lease, MAR)
    pay(owner, lease, 15000, D(2026, 1, 5))
    pay(owner, lease, 15000, D(2026, 2, 5))
    lease_services.end_lease(owner, lease, ended_on=D(2026, 3, 10), reason="Moved out")
    for invoice in (jan, feb, mar):
        invoice.refresh_from_db()
    assert (jan.status, feb.status, mar.status) == (PAID, PAID, VOID)  # paid months are never touched
    [march] = Invoice.objects.filter(lease=lease, lines__billing_month=MAR).exclude(status=VOID).distinct()
    assert march.total == Decimal("4838.71")  # 15000 x 10 / 31
    assert invoicing.lease_balance(lease) == Decimal("4838.71") == owed(owner, lease)
    assert not Invoice.objects.filter(lease=lease, lines__billing_month=D(2026, 4, 1)).exclude(status=VOID).exists()
    # Everyone reads the same sum.
    assert metrics.arrears(metrics.scope(owner), timezone.localdate()).total == Decimal("4838.71")


# ---------------------------------------------------------------------------
# 4. A rent change leaves old invoices alone
# ---------------------------------------------------------------------------


def test_a_rent_change_does_not_alter_invoices_before_it(owner, prop):
    lease = make_lease(owner, prop, rent=15000, due_day=5)
    jan, feb, mar = bill(lease, JAN), bill(lease, FEB), bill(lease, MAR)
    pay(owner, lease, 15000, D(2026, 1, 5))
    for i in (jan, feb, mar):
        i.refresh_from_db()
    before = {i.pk: (i.number, i.total, i.status, i.due_date) for i in (jan, feb, mar)}
    lease_services.add_rent_change(owner, lease, effective_from=D(2026, 4, 1), amount=17000)
    for i in (jan, feb, mar):
        i.refresh_from_db()
        assert (i.number, i.total, i.status, i.due_date) == before[i.pk]
    assert invoicing.lease_balance(lease) == Decimal("30000.00")
    april = bill(lease, D(2026, 4, 1))
    assert april.total == Decimal("17000.00")


def test_a_rent_change_part_way_through_never_touches_earlier_months(owner, prop):
    lease = make_lease(owner, prop, rent=15000, due_day=5)
    jan, feb = bill(lease, JAN), bill(lease, FEB)
    pay(owner, lease, 30000, D(2026, 2, 5))
    lease_services.add_rent_change(owner, lease, effective_from=D(2026, 3, 15), amount=17000)
    for i in (jan, feb):
        i.refresh_from_db()
        assert i.status == PAID and i.total == Decimal("15000.00")
    assert invoicing.lease_balance(lease) == 0
