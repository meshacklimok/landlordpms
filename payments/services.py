"""Recording, confirming, allocating and reversing payments (doc 11 §9, doc 13, D-043).

Maker/checker: `payments.record` enters a payment; it only counts once someone with
`payments.confirm` confirms it (at once, if the recorder holds both). Confirming posts one
PAYMENT credit to the lease ledger, allocates the money to open invoices oldest first and
issues a numbered receipt. Money not needed by open invoices stays on the payment as credit
and is swept into later invoices by `apply_credit`. Nothing is deleted: a pending payment is
rejected, a confirmed one is reversed.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import can, require
from audit import services as audit
from billing.models import Invoice, LedgerEntry
from core.money import ZERO, format_money, parse_money
from leases.models import Lease, LeaseTenant

from .models import Payment, PaymentAccount, PaymentAllocation

Status = Payment.Status


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def _same_org(actor: Membership, obj) -> None:
    if obj.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))


def _lock_lease(lease: Lease) -> Lease:
    return Lease.all_objects.select_for_update().select_related("unit__property", "organization").get(pk=lease.pk)


def _lock_payment(payment: Payment) -> Payment:
    return Payment.objects.select_for_update().get(pk=payment.pk)


def _amount(value) -> Decimal:
    try:
        return parse_money(value, allow_zero=False)
    except ValidationError as e:
        raise ValidationError({"amount": e.messages}) from None


def _paid_at(value) -> datetime.date:
    day = value or timezone.localdate()
    if day > timezone.localdate():
        raise ValidationError({"paid_at": _("The date cannot be in the future.")})
    return day


def _reason(value: str, message: str) -> str:
    reason = (value or "").strip()[:300]
    if not reason:
        raise ValidationError({"reason": message})
    return reason


def open_invoices(lease: Lease):
    """Invoices that still have something to pay, oldest due first: the default allocation order."""
    return (Invoice.objects.filter(lease=lease, status__in=Invoice.OPEN)
            .order_by("due_date", "period_start", "pk"))


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------


def _set_paid(invoice: Invoice, amount_paid: Decimal) -> None:
    invoice.amount_paid = amount_paid
    if amount_paid <= 0:
        invoice.status = Invoice.Status.ISSUED
    elif amount_paid >= invoice.total:
        invoice.status = Invoice.Status.PAID
    else:
        invoice.status = Invoice.Status.PARTIALLY_PAID
    invoice.save(update_fields=["amount_paid", "status", "updated_at"])


def _allocate(payment: Payment, plan: list[tuple[Invoice, Decimal]]) -> list[PaymentAllocation]:
    """Applies (invoice, amount) pairs to a payment. Invoices must already be locked."""
    made = []
    for invoice, amount in plan:
        if amount <= 0:
            continue
        existing = PaymentAllocation.objects.filter(payment=payment, invoice=invoice).first()
        if existing:
            PaymentAllocation.objects.filter(pk=existing.pk).update(amount=existing.amount + amount)
            existing.amount += amount
            made.append(existing)
        else:
            made.append(PaymentAllocation.objects.create(
                organization=payment.organization, payment=payment, invoice=invoice, amount=amount))
        _set_paid(invoice, invoice.amount_paid + amount)
    return made


def _fifo_plan(lease: Lease, available: Decimal) -> list[tuple[Invoice, Decimal]]:
    plan = []
    for invoice in open_invoices(lease).select_for_update():
        if available <= 0:
            break
        take = min(available, invoice.outstanding)
        if take > 0:
            plan.append((invoice, take))
            available -= take
    return plan


def _explicit_plan(payment: Payment, lease: Lease, allocations) -> list[tuple[Invoice, Decimal]]:
    """Validates a manager's chosen split: open invoices of this lease, within what each still owes."""
    plan, total, seen = [], ZERO, set()
    for invoice, raw in allocations:
        amount = _amount(raw)
        if invoice.pk in seen:
            raise ValidationError(_("Each invoice can appear only once."))
        seen.add(invoice.pk)
        locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if locked.lease_id != lease.pk or locked.status not in Invoice.OPEN:
            raise ValidationError(_("%(invoice)s is not an open invoice on this lease.") % {"invoice": locked})
        if amount > locked.outstanding:
            raise ValidationError(_("%(invoice)s only has %(left)s left to pay.")
                                  % {"invoice": locked, "left": format_money(locked.outstanding, locked.currency)})
        plan.append((locked, amount))
        total += amount
    if total > payment.unallocated:
        raise ValidationError(_("The allocations add up to more than the payment."))
    return plan


# ---------------------------------------------------------------------------
# Recording and review
# ---------------------------------------------------------------------------


def _payer(lease: Lease, tenant):
    if tenant is None:
        return lease.primary_tenant
    if not LeaseTenant.objects.filter(lease=lease, tenant=tenant).exists():
        raise ValidationError({"tenant": _("That tenant is not on this lease.")})
    return tenant


def _account(actor: Membership, account: PaymentAccount | None) -> PaymentAccount | None:
    if account is None:
        return None
    _same_org(actor, account)
    if account.archived_at is not None:
        raise ValidationError({"payment_account": _("That payment account is archived.")})
    return account


@transaction.atomic
def record_payment(actor: Membership, lease: Lease, *, amount, method: str, paid_at=None, reference="",
                   payment_account=None, tenant=None, allocations=None, request=None) -> Payment:
    """Enters money received. Confirmed at once if the recorder may confirm, else left for review."""
    _same_org(actor, lease)
    require(actor, "payments.record", lease.unit.property)
    lease = _lock_lease(lease)
    if lease.status == Lease.Status.DRAFT or lease.archived_at is not None:
        raise ValidationError(_("Payments can only be recorded on an activated lease."))
    if method not in Payment.Method.values:
        raise ValidationError({"method": _("Choose how it was paid.")})
    payment = Payment.objects.create(
        organization=lease.organization, lease=lease, tenant=_payer(lease, tenant), amount=_amount(amount),
        method=method, paid_at=_paid_at(paid_at), reference=(reference or "").strip()[:60],
        payment_account=_account(actor, payment_account), recorded_by=actor.user)
    audit.record("payment.record", actor=actor.user, organization=lease.organization, obj=payment, request=request,
                 changes={"lease": [None, lease.number], "amount": [None, str(payment.amount)],
                          "method": [None, method], "reference": [None, payment.reference]})
    if can(actor, "payments.confirm", lease.unit.property):
        _confirm(actor, payment, lease, allocations=allocations, request=request)
    return payment


def _confirm(actor: Membership, payment: Payment, lease: Lease, *, allocations=None, request=None) -> None:
    from .receipts import issue_receipt

    if allocations:
        require(actor, "payments.allocate", lease.unit.property)
        plan = _explicit_plan(payment, lease, allocations)
    else:
        plan = _fifo_plan(lease, payment.amount)
    payment.status = Status.CONFIRMED
    payment.confirmed_by = actor.user
    payment.confirmed_at = timezone.now()
    payment.save(update_fields=["status", "confirmed_by", "confirmed_at", "updated_at"])
    LedgerEntry.objects.create(organization=payment.organization, lease=lease, entry_date=payment.paid_at,
                               kind=LedgerEntry.Kind.PAYMENT, amount=-payment.amount, currency=lease.currency,
                               payment=payment, reason=payment.reference, created_by=actor.user)
    made = _allocate(payment, plan)
    receipt = issue_receipt(payment)
    audit.record("payment.confirm", actor=actor.user, organization=payment.organization, obj=payment,
                 request=request, changes={
                     "status": [Status.PENDING_REVIEW, Status.CONFIRMED], "receipt": [None, receipt.number],
                     "allocated": [None, {a.invoice.number: str(a.amount) for a in made}]})


@transaction.atomic
def confirm_payment(actor: Membership, payment: Payment, *, allocations=None, request=None) -> Payment:
    """Approves a payment waiting for review: posts it, allocates it and issues the receipt."""
    _same_org(actor, payment)
    require(actor, "payments.confirm", payment.lease.unit.property)
    lease = _lock_lease(payment.lease)
    payment = _lock_payment(payment)
    if payment.status != Status.PENDING_REVIEW:
        raise ValidationError(_("Only a payment waiting for review can be confirmed."))
    _confirm(actor, payment, lease, allocations=allocations, request=request)
    return payment


@transaction.atomic
def reject_payment(actor: Membership, payment: Payment, *, reason: str, request=None) -> Payment:
    """Turns down a payment waiting for review. It was never posted, so nothing is undone."""
    _same_org(actor, payment)
    require(actor, "payments.confirm", payment.lease.unit.property)
    reason = _reason(reason, _("Say why the payment is being rejected."))
    payment = _lock_payment(payment)
    if payment.status != Status.PENDING_REVIEW:
        raise ValidationError(_("Only a payment waiting for review can be rejected."))
    _mark_reversed(payment, actor, reason)
    audit.record("payment.reject", actor=actor.user, organization=payment.organization, obj=payment,
                 request=request, changes={"status": [Status.PENDING_REVIEW, Status.REVERSED],
                                           "reason": [None, reason]})
    return payment


def _mark_reversed(payment: Payment, actor: Membership, reason: str) -> None:
    payment.status = Status.REVERSED
    payment.reversed_by = actor.user
    payment.reversed_at = timezone.now()
    payment.reversal_reason = reason
    payment.save(update_fields=["status", "reversed_by", "reversed_at", "reversal_reason", "updated_at"])


# ---------------------------------------------------------------------------
# Reversal and credit
# ---------------------------------------------------------------------------


@transaction.atomic
def reverse_payment(actor: Membership, payment: Payment, *, reason: str, request=None) -> Payment:
    """Cancels a confirmed payment, e.g. a bounced cheque: invoices and balance go back as they were.

    The allocations stay as history; the invoices' paid amounts are taken back and the ledger
    gets one PAYMENT_REVERSAL debit. The receipt is kept and shown as reversed.
    """
    _same_org(actor, payment)
    require(actor, "payments.reverse", payment.lease.unit.property)
    reason = _reason(reason, _("Say why the payment is being reversed."))
    lease = _lock_lease(payment.lease)
    payment = _lock_payment(payment)
    if payment.status != Status.CONFIRMED:
        raise ValidationError(_("Only a confirmed payment can be reversed."))
    allocations = list(payment.allocations.order_by("pk"))
    invoices = {i.pk: i for i in Invoice.objects.select_for_update().filter(
        pk__in=[a.invoice_id for a in allocations]).order_by("pk")}
    for a in allocations:
        invoice = invoices[a.invoice_id]
        _set_paid(invoice, invoice.amount_paid - a.amount)
    LedgerEntry.objects.create(organization=payment.organization, lease=lease, entry_date=timezone.localdate(),
                               kind=LedgerEntry.Kind.PAYMENT_REVERSAL, amount=payment.amount,
                               currency=lease.currency, payment=payment, reason=reason, created_by=actor.user)
    _mark_reversed(payment, actor, reason)
    audit.record("payment.reverse", actor=actor.user, organization=payment.organization, obj=payment,
                 request=request, changes={"status": [Status.CONFIRMED, Status.REVERSED],
                                           "reason": [None, reason], "amount": [str(payment.amount), None]})
    return payment


def credit_payments(lease: Lease):
    """Confirmed payments on the lease with money not yet allocated to an invoice, oldest first."""
    return [p for p in Payment.objects.filter(lease=lease, status=Status.CONFIRMED)
            .prefetch_related("allocations").order_by("paid_at", "pk") if p.unallocated > 0]


def unallocated_credit(lease: Lease) -> Decimal:
    return sum((p.unallocated for p in credit_payments(lease)), ZERO)


@transaction.atomic
def apply_credit(actor: Membership, lease: Lease, *, request=None) -> list[PaymentAllocation]:
    """Puts money paid ahead towards invoices that are open now, oldest payment and invoice first."""
    _same_org(actor, lease)
    require(actor, "payments.allocate", lease.unit.property)
    lease = _lock_lease(lease)
    made = []
    for payment in credit_payments(lease):
        payment = _lock_payment(payment)
        made += _allocate(payment, _fifo_plan(lease, payment.unallocated))
    if made:
        audit.record("payment.apply_credit", actor=actor.user, organization=lease.organization, obj=lease,
                     request=request, changes={"allocated": [None, {
                         f"{a.payment.pk}→{a.invoice.number}": str(a.amount) for a in made}]})
    return made
