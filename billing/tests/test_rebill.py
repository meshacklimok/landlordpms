"""Issued invoices follow the lease when its terms change after billing in advance."""

import datetime
from decimal import Decimal

import pytest

from accounts.tests.factories import make_org, make_property
from billing import invoicing
from billing.models import ChargeType, Invoice, InvoiceLine, LedgerEntry
from leases import services as lease_services

from .test_invoicing import APR, FEB, MAR, bill, make_lease, water

pytestmark = pytest.mark.django_db

D = datetime.date
VOID, ISSUED = Invoice.Status.VOID, Invoice.Status.ISSUED


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def lease(owner):
    return make_lease(owner, make_property(owner.organization), due_day=5)


def live(lease, month):
    return Invoice.objects.filter(lease=lease, lines__billing_month=month).exclude(status=VOID).distinct()


def test_ending_a_lease_mid_month_rebills_that_month_and_voids_the_next(lease, owner):
    march, april = bill(lease, MAR), bill(lease, APR)
    lease_services.end_lease(owner, lease, ended_on=D(2026, 3, 10), reason="Moved out")
    march.refresh_from_db()
    april.refresh_from_db()
    assert march.status == april.status == VOID and "10 Mar 2026" in march.void_reason
    [again] = live(lease, MAR)
    assert again.total == Decimal("4838.71") and again.due_date == march.due_date == D(2026, 3, 5)
    assert not live(lease, APR).exists()
    assert invoicing.lease_balance(lease) == Decimal("4838.71")
    assert LedgerEntry.objects.filter(lease=lease, kind=LedgerEntry.Kind.INVOICE_VOID).count() == 2


def test_a_rent_change_rebills_the_months_already_billed(lease, owner):
    feb, march = bill(lease, FEB), bill(lease, MAR)
    lease_services.add_rent_change(owner, lease, effective_from=D(2026, 3, 15), amount=17000)
    feb.refresh_from_db()
    march.refresh_from_db()
    assert feb.status == ISSUED and march.status == VOID
    [again] = live(lease, MAR)
    assert again.total == Decimal("16096.77") and again.due_date == march.due_date


def test_a_month_that_does_not_change_is_left_alone(lease, owner):
    org = lease.organization
    org.prorate_partial_months = False
    org.save(update_fields=["prorate_partial_months"])
    march, april = bill(lease, MAR), bill(lease, APR)
    # Without proration March keeps the rate of its first day; only April changes.
    lease_services.add_rent_change(owner, lease, effective_from=D(2026, 3, 15), amount=17000)
    march.refresh_from_db()
    april.refresh_from_db()
    assert march.status == ISSUED and april.status == VOID
    assert live(lease, APR).get().total == Decimal("17000.00")


def test_an_invoice_with_payments_is_kept_for_manual_correction(lease, owner):
    march = bill(lease, MAR)
    Invoice.objects.filter(pk=march.pk).update(amount_paid=100, status=Invoice.Status.PARTIALLY_PAID)
    lease_services.add_rent_change(owner, lease, effective_from=D(2026, 3, 15), amount=17000)
    march.refresh_from_db()
    assert march.status == Invoice.Status.PARTIALLY_PAID and march.total == Decimal("15000.00")
    assert list(live(lease, MAR)) == [march]
    assert invoicing.rebill_from(lease, D(2026, 3, 15), actor=owner, reason="check") == [march]


def test_ending_a_charge_rebills_with_the_shorter_charge(lease, owner):
    lease_services.add_charge(owner, lease, charge_type=water(owner.organization), amount=500, active_from=FEB)
    march = bill(lease, MAR)
    charge = lease.charges.get()
    lease_services.end_charge(owner, charge, active_to=D(2026, 3, 15))
    march.refresh_from_db()
    assert march.status == VOID
    [again] = live(lease, MAR)
    assert sorted(ln.amount for ln in again.lines.all()) == [Decimal("241.94"), Decimal("15000.00")]  # 15/31
    # Moving the end later again bills the rest of the month.
    lease_services.end_charge(owner, charge, active_to=D(2026, 3, 31))
    assert live(lease, MAR).get().total == Decimal("15500.00")


def test_a_backdated_charge_is_billed_for_every_month_already_billed(lease, owner):
    bill(lease, FEB)
    bill(lease, MAR)
    garbage = ChargeType.objects.get(organization=owner.organization, category=ChargeType.Category.GARBAGE)
    lease_services.add_charge(owner, lease, charge_type=garbage, amount=300, active_from=D(2026, 2, 15))
    lines = InvoiceLine.objects.filter(lease=lease, charge_type=garbage).order_by("billing_month")
    assert [(ln.billing_month, ln.amount) for ln in lines] == [(FEB, Decimal("150.00")), (MAR, Decimal("300.00"))]
    assert Invoice.objects.filter(lease=lease, status=VOID).count() == 0
