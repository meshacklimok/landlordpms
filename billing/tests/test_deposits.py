"""The deposit sub-ledger (doc 14 A1, D-016)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.tests.factories import add_member, make_org, make_property
from audit.models import AuditEvent
from billing import deposits, invoicing
from billing.models import DepositEntry, LedgerEntry
from leases import services as lease_services
from properties import services as property_services

from .test_invoicing import FEB, JAN, MAR, bill, make_lease

pytestmark = pytest.mark.django_db

D = datetime.date
RENT, WATER = DepositEntry.DepositType.RENT, DepositEntry.DepositType.WATER


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def lease(owner):
    return make_lease(owner, make_property(owner.organization))


def test_deposits_are_held_per_type_and_never_touch_the_rent_balance(owner, lease):
    deposits.record_received(owner, lease, amount="30,000", entry_date=JAN, reference="QK12AB")
    deposits.record_received(owner, lease, amount=2000, deposit_type=WATER, entry_date=JAN)
    bill(lease, FEB)
    assert deposits.held(lease) == Decimal("32000.00")
    assert deposits.held_by_type(lease) == {RENT: Decimal("30000.00"), WATER: Decimal("2000.00")}
    # No fake zero balance: rent due and deposit held are shown apart.
    assert invoicing.lease_balance(lease) == Decimal("15000.00")
    assert AuditEvent.objects.filter(action="deposit.received").count() == 2


def test_a_deduction_needs_a_reason_the_capability_and_money_held(owner, lease):
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    with pytest.raises(ValidationError):
        deposits.deduct(owner, lease, amount=500, reason="  ")
    with pytest.raises(ValidationError):
        deposits.deduct(owner, lease, amount=30001, reason="Too much")
    with pytest.raises(ValidationError):
        deposits.deduct(owner, lease, amount=100, deposit_type=WATER, reason="None held")
    accountant = add_member(owner.organization, "accountant", all_properties=True)
    deposits.record_received(accountant, lease, amount=1000, entry_date=JAN)  # may record ...
    with pytest.raises(PermissionDenied):
        deposits.deduct(accountant, lease, amount=500, reason="Broken window")  # ... but not deduct
    entry = deposits.deduct(owner, lease, amount=4500, reason="Broken window")
    assert entry.amount == Decimal("-4500.00") and entry.ledger_entry is None
    assert deposits.held(lease) == Decimal("26500.00")


def test_a_deduction_applied_to_the_balance_credits_the_rent_ledger(owner, lease):
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    bill(lease, FEB)
    entry = deposits.deduct(owner, lease, amount=15000, reason="Last month's rent", apply_to_balance=True)
    assert entry.ledger_entry.kind == LedgerEntry.Kind.DEPOSIT_APPLIED
    assert entry.ledger_entry.amount == Decimal("-15000.00")
    assert invoicing.lease_balance(lease) == 0 and deposits.held(lease) == Decimal("15000.00")
    # Correcting it puts both ledgers back.
    deposits.reverse(owner, entry, reason="Tenant paid in cash")
    assert invoicing.lease_balance(lease) == Decimal("15000.00") and deposits.held(lease) == Decimal("30000.00")
    with pytest.raises(ValidationError):
        deposits.reverse(owner, entry, reason="again")


def test_refund_cannot_exceed_what_is_held(owner, lease):
    deposits.record_received(owner, lease, amount=10000, entry_date=JAN)
    with pytest.raises(ValidationError):
        deposits.refund(owner, lease, amount="10000.01")
    deposits.refund(owner, lease, amount=10000, reference="MPESA-REF")
    assert deposits.held(lease) == 0
    received = DepositEntry.objects.get(lease=lease, kind=DepositEntry.Kind.RECEIVED)
    with pytest.raises(ValidationError):  # reversing the receipt would leave a negative deposit
        deposits.reverse(owner, received, reason="Typo")


def test_deposit_moves_with_the_tenant_to_a_new_unit(owner, lease):
    deposits.record_received(owner, lease, amount=30000, entry_date=JAN)
    deposits.record_received(owner, lease, amount=2000, deposit_type=WATER, entry_date=JAN)
    other = property_services.create_unit(owner, lease.unit.property, code="B9")
    draft = lease_services.transfer_lease(owner, lease, unit=other, start_date=MAR, rent=18000)
    new = lease_services.activate_lease(owner, draft)
    assert deposits.held(lease) == 0
    assert deposits.held_by_type(new) == {RENT: Decimal("30000.00"), WATER: Decimal("2000.00")}
    out = DepositEntry.objects.get(lease=lease, kind=DepositEntry.Kind.TRANSFERRED, deposit_type=RENT)
    into = DepositEntry.objects.get(lease=new, kind=DepositEntry.Kind.TRANSFERRED, deposit_type=RENT)
    assert out.counterpart == into and into.counterpart == out and into.entry_date == MAR
    assert new.number in out.reason
    with pytest.raises(ValidationError):
        deposits.reverse(owner, into, reason="no")
    statement = deposits.clearance_statement(lease)
    assert statement["held"] == 0
    assert [(t["code"], t["received"], t["transferred"]) for t in statement["types"]] == [
        (RENT, Decimal("30000.00"), Decimal("-30000.00")), (WATER, Decimal("2000.00"), Decimal("-2000.00"))]


def test_deposits_respect_organization_scope_and_drafts(owner, lease):
    with pytest.raises(PermissionDenied):
        deposits.record_received(make_org(), lease, amount=1)
    scoped = add_member(owner.organization, "manager", properties=[make_property(owner.organization)])
    with pytest.raises(PermissionDenied):
        deposits.record_received(scoped, lease, amount=1)
    with pytest.raises(ValidationError):
        deposits.record_received(owner, lease, amount=1, entry_date=D.today() + datetime.timedelta(days=1))
    with pytest.raises(ValidationError):
        deposits.record_received(owner, lease, amount=0)


def test_deposit_rows_are_append_only(owner, lease):
    entry = deposits.record_received(owner, lease, amount=100)
    entry.amount = Decimal("1")
    with pytest.raises(ValueError):
        entry.save()
    with pytest.raises(ValueError):
        entry.delete()
