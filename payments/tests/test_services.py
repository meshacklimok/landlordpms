"""Recording, review, allocation, reversal and credit (doc 11 §9, doc 13 maker/checker, D-043)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from accounts.tests.factories import add_member, make_org, make_property
from audit.models import AuditEvent
from billing.invoicing import lease_balance
from billing.models import Invoice, LedgerEntry
from billing.tests.test_invoicing import FEB, JAN, MAR, bill, make_lease
from payments import services
from payments.models import Payment, PaymentAccount, PaymentAllocation
from tenants import services as tenant_services

pytestmark = pytest.mark.django_db

D = datetime.date
Status = Payment.Status


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


@pytest.fixture
def lease(owner, prop):
    return make_lease(owner, prop)


@pytest.fixture
def accountant(owner):
    return add_member(owner.organization, "accountant", all_properties=True)


def pay(actor, lease, amount, **kw):
    kw.setdefault("method", Payment.Method.CASH)
    kw.setdefault("paid_at", D(2026, 2, 3))
    return services.record_payment(actor, lease, amount=amount, **kw)


def refresh(*objs):
    for o in objs:
        o.refresh_from_db()


# ---------------------------------------------------------------------------
# Recording and maker/checker
# ---------------------------------------------------------------------------


def test_recorder_who_may_confirm_posts_allocates_and_receipts_at_once(owner, lease):
    invoice = bill(lease, FEB)
    payment = pay(owner, lease, "15000", reference=" QAB12CD ")
    refresh(invoice)
    assert payment.status == Status.CONFIRMED and payment.confirmed_by == owner.user
    assert payment.reference == "QAB12CD" and payment.tenant == lease.primary_tenant
    assert (invoice.status, invoice.amount_paid) == (Invoice.Status.PAID, Decimal("15000.00"))
    entry = LedgerEntry.objects.get(payment=payment)
    assert (entry.kind, entry.amount, entry.entry_date) == (LedgerEntry.Kind.PAYMENT, Decimal("-15000.00"),
                                                            D(2026, 2, 3))
    assert lease_balance(lease) == 0
    assert payment.receipt.number == "RCT-2026-000001"
    assert set(AuditEvent.objects.filter(action__startswith="payment.").values_list("action", flat=True)) == {
        "payment.record", "payment.confirm"}


def test_recorder_without_confirm_leaves_it_pending_and_posts_nothing(accountant, lease):
    invoice = bill(lease, FEB)
    payment = pay(accountant, lease, "5000")
    refresh(invoice)
    assert payment.status == Status.PENDING_REVIEW and payment.recorded_by == accountant.user
    assert invoice.amount_paid == 0 and invoice.status == Invoice.Status.ISSUED
    assert not LedgerEntry.objects.filter(payment=payment).exists()
    assert not hasattr(payment, "receipt") or not Payment.objects.filter(pk=payment.pk, receipt__isnull=False)
    assert lease_balance(lease) == Decimal("15000.00")


def test_checker_confirms_a_pending_payment(owner, accountant, lease):
    invoice = bill(lease, FEB)
    payment = services.confirm_payment(owner, pay(accountant, lease, "5000"))
    refresh(invoice)
    assert payment.status == Status.CONFIRMED and payment.confirmed_by == owner.user
    assert (invoice.status, invoice.amount_paid) == (Invoice.Status.PARTIALLY_PAID, Decimal("5000.00"))
    assert lease_balance(lease) == Decimal("10000.00")
    with pytest.raises(ValidationError):
        services.confirm_payment(owner, payment)


def test_maker_cannot_confirm_their_own_payment(accountant, lease):
    payment = pay(accountant, lease, "5000")
    with pytest.raises(PermissionDenied):
        services.confirm_payment(accountant, payment)


def test_reject_needs_a_reason_and_posts_nothing(owner, accountant, lease):
    bill(lease, FEB)
    payment = pay(accountant, lease, "5000")
    with pytest.raises(ValidationError):
        services.reject_payment(owner, payment, reason="  ")
    services.reject_payment(owner, payment, reason="Duplicate entry")
    refresh(payment)
    assert payment.status == Status.REVERSED and payment.reversal_reason == "Duplicate entry"
    assert not LedgerEntry.objects.filter(payment=payment).exists()
    with pytest.raises(ValidationError):
        services.confirm_payment(owner, payment)


def test_viewer_and_caretaker_cannot_record(owner, lease):
    for role in ("viewer", "caretaker"):
        member = add_member(owner.organization, role, all_properties=True)
        with pytest.raises(PermissionDenied):
            pay(member, lease, "100")
    assert not Payment.objects.exists()


def test_member_without_the_property_cannot_record(owner, prop, lease):
    other = make_property(owner.organization)
    member = add_member(owner.organization, "accountant", properties=[other])
    with pytest.raises(PermissionDenied):
        pay(member, lease, "100")


def test_other_organization_cannot_touch_the_lease(lease):
    stranger = make_org()
    with pytest.raises(PermissionDenied):
        pay(stranger, lease, "100")


@pytest.mark.parametrize("amount", ["0", "-5", "abc", ""])
def test_amount_must_be_positive_money(owner, lease, amount):
    with pytest.raises(ValidationError) as e:
        pay(owner, lease, amount)
    assert "amount" in e.value.message_dict


def test_future_date_and_unknown_method_are_rejected(owner, lease):
    with pytest.raises(ValidationError):
        pay(owner, lease, "100", paid_at=datetime.date.today() + datetime.timedelta(days=1))
    with pytest.raises(ValidationError):
        pay(owner, lease, "100", method="BITCOIN")


def test_payer_must_be_on_the_lease(owner, lease):
    outsider = tenant_services.create_tenant(owner, name="Outsider", phone="0799000111")
    with pytest.raises(ValidationError):
        pay(owner, lease, "100", tenant=outsider)


def test_payment_account_must_be_live_and_ours(owner, lease):
    ours = PaymentAccount.objects.create(organization=owner.organization, type=PaymentAccount.Type.PAYBILL,
                                         number="123456", display_name="Main paybill")
    payment = pay(owner, lease, "100", payment_account=ours)
    assert payment.payment_account == ours
    theirs = PaymentAccount.objects.create(organization=make_org().organization, type=PaymentAccount.Type.TILL,
                                           number="999", display_name="Theirs")
    with pytest.raises(PermissionDenied):
        pay(owner, lease, "100", payment_account=theirs)


def test_draft_lease_takes_no_payments(owner, prop):
    from leases import services as lease_services
    from properties import services as property_services

    unit = property_services.create_unit(owner, prop, code="D1")
    tenant = tenant_services.create_tenant(owner, name="Draft", phone="0711000222")
    draft = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=JAN, end_date=None,
                                        rent=10000)
    with pytest.raises(ValidationError):
        pay(owner, draft, "100")


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------


def test_fifo_pays_the_oldest_due_invoice_first_and_splits(owner, lease):
    jan, feb, mar = bill(lease, JAN), bill(lease, FEB), bill(lease, MAR)
    payment = pay(owner, lease, "20000", paid_at=D(2026, 3, 2))
    refresh(jan, feb, mar)
    assert [(i.status, i.amount_paid) for i in (jan, feb, mar)] == [
        (Invoice.Status.PAID, Decimal("15000.00")), (Invoice.Status.PARTIALLY_PAID, Decimal("5000.00")),
        (Invoice.Status.ISSUED, Decimal("0.00"))]
    assert [(a.invoice, a.amount) for a in payment.allocations.all()] == [
        (jan, Decimal("15000.00")), (feb, Decimal("5000.00"))]
    assert payment.unallocated == 0


def test_overpayment_is_held_as_credit(owner, lease):
    invoice = bill(lease, FEB)
    payment = pay(owner, lease, "20000")
    refresh(invoice)
    assert invoice.status == Invoice.Status.PAID
    assert payment.unallocated == Decimal("5000.00")
    assert lease_balance(lease) == Decimal("-5000.00")
    assert services.unallocated_credit(lease) == Decimal("5000.00")


def test_payment_with_nothing_open_is_all_credit(owner, lease):
    payment = pay(owner, lease, "3000")
    assert payment.status == Status.CONFIRMED and not payment.allocations.exists()
    assert lease_balance(lease) == Decimal("-3000.00")


def test_apply_credit_sweeps_into_later_invoices(owner, lease):
    pay(owner, lease, "20000", paid_at=D(2026, 1, 5))
    jan, feb = bill(lease, JAN), bill(lease, FEB)
    made = services.apply_credit(owner, lease)
    refresh(jan, feb)
    assert [(a.invoice, a.amount) for a in made] == [(jan, Decimal("15000.00")), (feb, Decimal("5000.00"))]
    assert feb.status == Invoice.Status.PARTIALLY_PAID
    assert services.unallocated_credit(lease) == 0
    assert services.apply_credit(owner, lease) == []
    assert AuditEvent.objects.filter(action="payment.apply_credit").count() == 1


def test_apply_credit_merges_into_an_existing_allocation(owner, lease):
    feb = bill(lease, FEB)
    first = pay(owner, lease, "10000")
    second = pay(owner, lease, "8000")
    refresh(feb)
    assert feb.status == Invoice.Status.PAID and second.unallocated == Decimal("3000.00")
    mar = bill(lease, MAR)
    services.apply_credit(owner, lease)
    refresh(mar)
    assert mar.amount_paid == Decimal("3000.00")
    assert first.allocations.count() == 1 and second.allocations.count() == 2


def test_apply_credit_needs_allocate(owner, lease):
    viewer = add_member(owner.organization, "viewer", all_properties=True)
    with pytest.raises(PermissionDenied):
        services.apply_credit(viewer, lease)


def test_explicit_allocation_overrides_fifo(owner, lease):
    jan, feb = bill(lease, JAN), bill(lease, FEB)
    payment = pay(owner, lease, "10000", allocations=[(feb, "6000"), (jan, "1000")])
    refresh(jan, feb)
    assert (jan.amount_paid, feb.amount_paid) == (Decimal("1000.00"), Decimal("6000.00"))
    assert payment.unallocated == Decimal("3000.00")


def test_explicit_allocation_is_validated(owner, lease, prop):
    jan, feb = bill(lease, JAN), bill(lease, FEB)
    other = bill(make_lease(owner, prop, code="B2"), FEB)
    bad = [
        [(jan, "16000")],                       # more than the invoice still owes
        [(jan, "8000"), (feb, "8000")],         # more than the payment
        [(jan, "100"), (jan, "100")],           # same invoice twice
        [(other, "100")],                       # another lease's invoice
        [(jan, "0")],
    ]
    for allocations in bad:
        with pytest.raises(ValidationError):
            pay(owner, lease, "10000", allocations=allocations)
    assert not Payment.objects.exists()


def test_checker_can_choose_the_split_when_confirming(owner, accountant, lease):
    feb = bill(lease, FEB)
    manager = add_member(owner.organization, "manager", all_properties=True)
    payment = pay(accountant, lease, "5000")
    services.confirm_payment(manager, payment, allocations=[(feb, "5000")])
    refresh(feb)
    assert feb.amount_paid == Decimal("5000.00")


# ---------------------------------------------------------------------------
# Reversal
# ---------------------------------------------------------------------------


def test_reversal_rolls_invoices_and_balance_back(owner, lease):
    feb = bill(lease, FEB)
    first = pay(owner, lease, "10000")
    second = pay(owner, lease, "5000")
    refresh(feb)
    assert feb.status == Invoice.Status.PAID and lease_balance(lease) == 0
    services.reverse_payment(owner, second, reason="Cheque bounced")
    refresh(feb, second)
    assert (feb.status, feb.amount_paid) == (Invoice.Status.PARTIALLY_PAID, Decimal("10000.00"))
    assert lease_balance(lease) == Decimal("5000.00")
    assert second.status == Status.REVERSED and second.reversed_by == owner.user
    entry = LedgerEntry.objects.get(payment=second, kind=LedgerEntry.Kind.PAYMENT_REVERSAL)
    assert entry.amount == Decimal("5000.00") and entry.reason == "Cheque bounced"
    assert second.receipt.number  # kept as history
    services.reverse_payment(owner, first, reason="Wrong tenant")
    refresh(feb)
    assert (feb.status, feb.amount_paid) == (Invoice.Status.ISSUED, Decimal("0.00"))
    assert lease_balance(lease) == Decimal("15000.00")


def test_reversal_of_credit_only_payment(owner, lease):
    payment = pay(owner, lease, "3000")
    services.reverse_payment(owner, payment, reason="Entered twice")
    assert lease_balance(lease) == 0


def test_reversal_rules(owner, accountant, lease):
    manager = add_member(owner.organization, "manager", all_properties=True)
    confirmed = pay(owner, lease, "1000")
    with pytest.raises(PermissionDenied):
        services.reverse_payment(manager, confirmed, reason="x")  # reverse is owner-only by default
    with pytest.raises(ValidationError):
        services.reverse_payment(owner, confirmed, reason="")
    services.reverse_payment(owner, confirmed, reason="Mistake")
    with pytest.raises(ValidationError):
        services.reverse_payment(owner, confirmed, reason="Again")
    pending = pay(accountant, lease, "1000")
    with pytest.raises(ValidationError):
        services.reverse_payment(owner, pending, reason="Not confirmed")


def test_paid_invoice_cannot_be_voided_until_payment_reversed(owner, lease):
    from billing import invoicing

    feb = bill(lease, FEB)
    payment = pay(owner, lease, "15000")
    refresh(feb)
    with pytest.raises(ValidationError):
        invoicing.void_invoice(owner, feb, reason="Wrong")
    services.reverse_payment(owner, payment, reason="Wrong lease")
    refresh(feb)
    invoicing.void_invoice(owner, feb, reason="Wrong")


# ---------------------------------------------------------------------------
# Database guards
# ---------------------------------------------------------------------------


def test_ledger_payment_entries_need_a_payment_and_post_once(owner, lease):
    payment = pay(owner, lease, "1000")
    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerEntry.objects.create(organization=owner.organization, lease=lease, entry_date=FEB,
                                   kind=LedgerEntry.Kind.PAYMENT, amount=Decimal("-1"), currency="KES")
    with pytest.raises(IntegrityError), transaction.atomic():
        LedgerEntry.objects.create(organization=owner.organization, lease=lease, entry_date=FEB,
                                   kind=LedgerEntry.Kind.PAYMENT, amount=Decimal("-1"), currency="KES",
                                   payment=payment)


def test_allocation_amount_must_be_positive(owner, lease):
    feb = bill(lease, FEB)
    payment = pay(owner, lease, "1000")
    with pytest.raises(IntegrityError), transaction.atomic():
        PaymentAllocation.objects.create(organization=owner.organization, payment=payment, invoice=feb,
                                         amount=Decimal("0"))
