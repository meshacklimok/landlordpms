"""Invoice lists, the lease statement and arrears aging (doc 11 §8)."""

import datetime
from decimal import Decimal

import pytest

from accounts.tests.factories import add_member, make_org, make_property
from billing import deposits, invoicing, selectors
from billing.models import Invoice, LedgerEntry

from .test_invoicing import APR, FEB, JAN, MAR, bill, make_lease

pytestmark = pytest.mark.django_db

D = datetime.date


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def lease(owner):
    return make_lease(owner, make_property(owner.organization), due_day=5)


def test_statement_runs_a_balance_and_brings_earlier_rows_forward(owner, lease):
    invoicing.set_opening_balance(owner, lease, amount=4000, as_of=D(2025, 12, 20))
    bill(lease, JAN)
    feb = bill(lease, FEB)
    invoicing.void_invoice(owner, feb, reason="Wrong")
    whole = selectors.statement(lease)
    assert [r.balance for r in whole["rows"]] == [Decimal("4000"), Decimal("19000"), Decimal("34000"),
                                                  Decimal("19000")]
    assert whole["rows"][-1].credit == Decimal("15000") and whole["rows"][0].debit == Decimal("4000")
    assert whole["closing"] == invoicing.lease_balance(lease)
    later = selectors.statement(lease, start=D(2025, 12, 21))
    assert later["brought_forward"] == Decimal("4000") and len(later["rows"]) == 3


def test_credits_pay_the_oldest_debt_first(owner, lease):
    invoicing.set_opening_balance(owner, lease, amount=10000, as_of=D(2025, 12, 31))
    bill(lease, JAN)
    bill(lease, FEB)
    deposits.record_received(owner, lease, amount=12000, entry_date=JAN)
    deposits.deduct(owner, lease, amount=12000, reason="Towards arrears", apply_to_balance=True)
    entries = list(LedgerEntry.objects.filter(lease=lease).select_related("invoice"))
    row = selectors.lease_arrears(lease, entries, D(2026, 3, 10))
    # 12000 clears the 10000 brought forward, then 2000 of January.
    assert [(d.due, d.amount) for d in row.debts] == [(D(2026, 1, 5), Decimal("13000")),
                                                      (D(2026, 2, 5), Decimal("15000"))]
    assert row.buckets == {"current": 0, "d1_30": 0, "d31_60": Decimal("15000"), "d61_90": Decimal("13000"),
                           "d90": 0}
    assert row.balance == Decimal("28000") and row.days_overdue(D(2026, 3, 10)) == 64


def test_a_void_cancels_its_own_invoice_not_the_oldest(owner, lease):
    bill(lease, JAN)
    feb = bill(lease, FEB)
    invoicing.void_invoice(owner, feb, reason="Wrong")
    entries = list(LedgerEntry.objects.filter(lease=lease).select_related("invoice"))
    [debt] = selectors.lease_arrears(lease, entries, D(2026, 3, 10)).debts
    assert (debt.due, debt.amount, debt.invoice.lines.get().billing_month) == (D(2026, 1, 5), Decimal("15000"), JAN)


def test_arrears_list_is_scoped_and_skips_leases_not_yet_overdue(owner, lease):
    other_prop = make_property(owner.organization)
    late = make_lease(owner, other_prop, code="B2", start=MAR, due_day=5)
    bill(lease, JAN)
    bill(late, APR, today=D(2026, 3, 28))
    today = D(2026, 3, 1)
    rows = selectors.arrears(owner, today)
    assert [r.lease for r in rows] == [lease]
    assert {r.lease for r in selectors.arrears(owner, today, overdue_only=False)} == {lease, late}
    assert selectors.aging_totals(rows)["total"] == Decimal("15000")
    scoped = add_member(owner.organization, "manager", properties=[other_prop])
    assert selectors.arrears(scoped, today) == []
    assert selectors.arrears(make_org(), today) == []


def test_invoice_list_is_scoped_and_filtered(owner, lease):
    other_prop = make_property(owner.organization)
    theirs = bill(make_lease(owner, other_prop, code="C3"), JAN)
    mine = bill(lease, JAN)
    assert set(selectors.visible_invoices(owner)) == {mine, theirs}
    scoped = add_member(owner.organization, "manager", properties=[other_prop])
    assert list(selectors.visible_invoices(scoped)) == [theirs]
    assert not selectors.visible_invoices(make_org()).exists()
    qs = selectors.visible_invoices(owner)
    assert list(selectors.filter_invoices(qs, q="Tenant C3")) == [theirs]
    assert list(selectors.filter_invoices(qs, q=mine.number)) == [mine]
    assert set(selectors.filter_invoices(qs, overdue=True, today=D(2026, 2, 1))) == {mine, theirs}
    invoicing.void_invoice(owner, mine, reason="Wrong")
    assert list(selectors.filter_invoices(qs, status="void")) == [mine]
    assert list(selectors.filter_invoices(qs, status="open")) == [theirs]
    assert Invoice.objects.count() == 2
