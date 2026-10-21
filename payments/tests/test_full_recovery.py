"""A tenant who pays has every shilling recovered: oldest bill first, nothing lost, nothing counted twice (D-183)."""

import datetime
from decimal import Decimal

import pytest

from billing import invoicing
from billing.models import ChargeType, Invoice, LedgerEntry
from billing.services import ensure_default_charge_types
from billing.tests.test_invoicing import bill, make_lease
from leases import services as lease_services
from payments import services as payment_services
from payments.models import PaymentAllocation

pytestmark = pytest.mark.django_db
D = datetime.date


def money(v):
    return Decimal(v).quantize(Decimal("0.01"))


@pytest.fixture
def owner():
    from accounts.tests.factories import make_org

    return make_org()


@pytest.fixture
def arrears(owner):
    """Three months billed (rent 20,000 + garbage 500 + water-style fixed 1,000 each) and nothing paid."""
    from accounts.tests.factories import make_property

    prop = make_property(owner.organization, name="Acacia Court")
    lease = make_lease(owner, prop, rent=20000)
    ensure_default_charge_types(owner.organization)
    for cat, amt in ((ChargeType.Category.GARBAGE, 500), (ChargeType.Category.WATER, 1000)):
        ct = ChargeType.objects.get(organization=owner.organization, category=cat)
        lease_services.add_charge(owner, lease, charge_type=ct, amount=amt)
    for month in (D(2026, 1, 1), D(2026, 2, 1), D(2026, 3, 1)):
        bill(lease, month)
    return lease


def check_books(lease):
    """Whatever happened, invoices, ledger and allocations must agree."""
    for inv in Invoice.objects.filter(lease=lease):
        # A reversed payment keeps its allocations as history; only confirmed payments count.
        paid = sum(a.amount for a in PaymentAllocation.objects.filter(invoice=inv, payment__status="CONFIRMED"))
        assert inv.amount_paid == paid <= inv.total
    ledger = sum(e.amount for e in LedgerEntry.objects.filter(lease=lease))
    assert ledger == invoicing.lease_balance(lease)
    owed = sum(i.total - i.amount_paid for i in Invoice.objects.filter(lease=lease))
    credit = payment_services.unallocated_credit(lease)
    assert ledger == owed - credit


def test_paying_everything_in_one_go_clears_every_bill(owner, arrears):
    assert invoicing.lease_balance(arrears) == money(64500)
    payment_services.record_payment(owner, arrears, amount=64500, method="CASH", paid_at=D(2026, 3, 5))
    assert invoicing.lease_balance(arrears) == 0
    assert {i.status for i in Invoice.objects.filter(lease=arrears)} == {Invoice.Status.PAID}
    assert payment_services.unallocated_credit(arrears) == 0
    check_books(arrears)


def test_part_payments_clear_the_oldest_bill_first_and_then_the_rest(owner, arrears):
    payment_services.record_payment(owner, arrears, amount=30000, method="MPESA", paid_at=D(2026, 3, 1),
                                    reference="QWE123")
    jan, feb, mar = Invoice.objects.filter(lease=arrears).order_by("period_start")
    assert (jan.status, feb.amount_paid) == (Invoice.Status.PAID, money(8500))
    assert mar.amount_paid == 0
    check_books(arrears)
    payment_services.record_payment(owner, arrears, amount=34500, method="BANK", paid_at=D(2026, 3, 9))
    assert invoicing.lease_balance(arrears) == 0
    check_books(arrears)


def test_an_overpayment_is_kept_as_credit_and_pays_the_next_bill(owner, arrears):
    payment_services.record_payment(owner, arrears, amount=70000, method="CASH", paid_at=D(2026, 3, 5))
    assert invoicing.lease_balance(arrears) == money(-5500)
    assert payment_services.unallocated_credit(arrears) == money(5500)
    check_books(arrears)
    april = bill(arrears, D(2026, 4, 1))
    april.refresh_from_db()
    assert (april.amount_paid, april.status) == (money(5500), Invoice.Status.PARTIALLY_PAID)
    assert invoicing.lease_balance(arrears) == money(16000)
    assert payment_services.unallocated_credit(arrears) == 0
    check_books(arrears)


def test_the_form_refuses_a_reference_that_was_already_used(owner, arrears):
    from payments.forms import PaymentForm

    payment_services.record_payment(owner, arrears, amount=5000, method="CASH", paid_at=D(2026, 3, 1),
                                    reference="CHQ0042")
    data = {"amount": "5000", "method": "CASH", "paid_at": "2026-03-02", "reference": "CHQ0042"}
    form = PaymentForm(data, lease=arrears)
    assert not form.is_valid() and "reference" in form.errors
    assert PaymentForm({**data, "allow_duplicate": "on"}, lease=arrears).is_valid()
    assert arrears.payments.count() == 1 and invoicing.lease_balance(arrears) == money(59500)
    check_books(arrears)


def test_reversing_a_payment_puts_the_debt_back_exactly(owner, arrears):
    p = payment_services.record_payment(owner, arrears, amount=64500, method="CASH", paid_at=D(2026, 3, 5))
    payment_services.reverse_payment(owner, p, reason="Cheque bounced")
    assert invoicing.lease_balance(arrears) == money(64500)
    assert Invoice.objects.filter(lease=arrears, amount_paid__gt=0).count() == 0
    check_books(arrears)
    payment_services.record_payment(owner, arrears, amount=64500, method="BANK", paid_at=D(2026, 3, 12))
    assert invoicing.lease_balance(arrears) == 0
    check_books(arrears)
