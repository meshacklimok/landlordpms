"""Payment requests to a tenant's phone: STK push, Lipa na M-Pesa Online (D-045 item 8).

Staff with `payments.record` send a request from the lease, or the tenant starts one from the
lease's payment link (`paylinks`, D-046 item 1). The tenant approves it with their
M-Pesa PIN; Safaricom's callback then carries the receipt number, and the payment is confirmed on
that lease with confidence (the request named the lease). A C2B confirmation for the same receipt
is a duplicate: `trans_id` is unique, so whichever arrives second changes nothing. [VERIFY whether
Daraja sends both]
"""

import datetime
import logging
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import require
from audit import services as audit
from core.phone import InvalidPhoneNumber, normalize_phone
from leases.models import Lease
from leases.services import visible_leases
from payments.models import PaymentAccount, PropertyPaymentAccount

from . import c2b
from .daraja import DarajaError, get_client
from .models import DarajaCredentials, MpesaTransaction, StkRequest
from .services import _check_urls, callback_urls

logger = logging.getLogger(__name__)

Status = StkRequest.Status
MAX_AMOUNT = Decimal("250000")  # M-Pesa's limit per transaction [VERIFY]
RESEND_AFTER = datetime.timedelta(minutes=2)
# Daraja answers a query for a request still on the phone with an error, not a result. [VERIFY]
STILL_PROCESSING = "500.001.1001"


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def stk_accounts(lease: Lease) -> list[DarajaCredentials]:
    """Accounts that can send a request for this lease: a Paybill with a passkey that collects for
    the lease's property, or for no property in particular. The property's default comes first."""
    served = PropertyPaymentAccount.objects.filter(property_id=lease.unit.property_id)
    unassigned = ~Q(payment_account__properties__isnull=False)
    creds = (DarajaCredentials.objects.filter(organization_id=lease.organization_id,
                                              payment_account__archived_at__isnull=True)
             .filter(Q(payment_account__in=served.values("payment_account_id")) | unassigned)
             .select_related("payment_account").distinct())
    defaults = set(served.filter(is_default=True).values_list("payment_account_id", flat=True))
    usable = [c for c in creds if c.can_request_payment]
    return sorted(usable, key=lambda c: (c.payment_account_id not in defaults, c.payment_account.display_name))


def _phone(value: str) -> str:
    try:
        phone = normalize_phone(value)
    except InvalidPhoneNumber:
        raise ValidationError({"phone": _("Enter a valid phone number.")}) from None
    if not phone.startswith("+254"):
        raise ValidationError({"phone": _("M-Pesa requests go to Kenyan numbers only.")})
    return phone


def _whole_shillings(value) -> Decimal:
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValidationError({"amount": _("Enter an amount.")}) from None
    if not amount.is_finite() or amount != amount.to_integral_value():
        raise ValidationError({"amount": _("M-Pesa requests are for whole shillings.")})
    if amount < 1 or amount > MAX_AMOUNT:
        raise ValidationError({"amount": _("Enter an amount from 1 to %(max)s.") % {"max": f"{MAX_AMOUNT:,.0f}"}})
    return amount


def request_payment(actor: Membership, lease: Lease, *, phone: str, amount, payment_account: PaymentAccount = None,
                    request=None) -> StkRequest:
    """Sends a payment request to the phone. A refusal by Safaricom is kept as FAILED and raised."""
    if lease.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, "payments.record", lease.unit.property)
    if not visible_leases(actor, Lease.objects.filter(pk=lease.pk)).exists():
        raise PermissionDenied(_("That record belongs to another organization."))
    if lease.status == Lease.Status.DRAFT or lease.archived_at is not None:
        raise ValidationError(_("Payments can only be requested on an activated lease."))
    return send(lease, phone=phone, amount=amount, payment_account=payment_account, requested_by=actor.user,
                request=request)


def send(lease: Lease, *, phone: str, amount, payment_account: PaymentAccount = None, requested_by=None,
         pay_link=None, request=None) -> StkRequest:
    """Checks the phone and amount, then sends the prompt. Callers check who may send it."""
    phone, amount = _phone(phone), _whole_shillings(amount)
    accounts = stk_accounts(lease)
    if payment_account is not None:
        accounts = [c for c in accounts if c.payment_account_id == payment_account.pk]
    if not accounts:
        raise ValidationError(_("No Paybill with a passkey collects for this property. Set one up in M-Pesa settings."))
    creds = accounts[0]
    url = callback_urls(creds)["stk"]
    _check_urls(creds, {"stk": url})
    recent = StkRequest.objects.filter(lease=lease, phone=phone, status=Status.PENDING,
                                       created_at__gte=timezone.now() - RESEND_AFTER)
    if recent.exists():
        raise ValidationError(_("A request was sent to this phone less than two minutes ago. Wait for the tenant "
                                "to answer it."))

    stk = StkRequest.objects.create(
        organization_id=lease.organization_id, payment_account=creds.payment_account, lease=lease,
        requested_by=requested_by, pay_link=pay_link, phone=phone, amount=amount,
        account_reference=lease.unit.payment_reference[:12])
    changes = {"lease": [None, lease.number], "phone": [None, phone], "amount": [None, str(amount)]}
    if pay_link is not None:
        changes["via"] = [None, "payment link"]
    try:
        data = get_client(creds).stk_push(phone=phone, amount=int(amount), account_reference=stk.account_reference,
                                          description="Rent", callback_url=url)
    except DarajaError as e:
        _finish(stk, Status.FAILED, "", str(e))
        audit.record("mpesa.stk_request", actor=requested_by, organization=lease.organization, obj=stk,
                     request=request, changes={**changes, "error": [None, str(e)]})
        raise ValidationError(_("Safaricom did not send the request: %(error)s") % {"error": e}) from None
    stk.merchant_request_id = str(data.get("MerchantRequestID", ""))[:60]
    stk.checkout_request_id = str(data["CheckoutRequestID"])[:60]
    stk.save(update_fields=["merchant_request_id", "checkout_request_id", "updated_at"])
    audit.record("mpesa.stk_request", actor=requested_by, organization=lease.organization, obj=stk, request=request,
                 changes=changes)
    return stk


def _finish(stk: StkRequest, status: str, code: str, desc: str, tx: MpesaTransaction | None = None) -> None:
    stk.status = status
    stk.result_code = code[:20]
    stk.result_desc = desc[:300]
    stk.completed_at = timezone.now()
    if tx is not None:
        stk.transaction = tx
    stk.save()


# ---------------------------------------------------------------------------
# The callback
# ---------------------------------------------------------------------------


def _callback(payload: dict) -> dict:
    body = payload.get("Body") if isinstance(payload, dict) else None
    cb = body.get("stkCallback") if isinstance(body, dict) else None
    if not isinstance(cb, dict) or not cb.get("CheckoutRequestID"):
        raise c2b.BadCallback("stkCallback or CheckoutRequestID missing")
    return cb


def _items(cb: dict) -> dict:
    meta = cb.get("CallbackMetadata")
    items = meta.get("Item") if isinstance(meta, dict) else None
    if not isinstance(items, list):
        return {}
    return {i.get("Name"): i.get("Value") for i in items if isinstance(i, dict)}


def _store(creds: DarajaCredentials, stk: StkRequest, receipt: str, items: dict, payload: dict) -> MpesaTransaction:
    """The transaction for a paid request, created RECEIVED (or the one a C2B confirmation already made)."""
    existing = MpesaTransaction.objects.select_for_update().filter(trans_id=receipt).first()
    if existing is not None:
        return existing
    problems, notes = [], []
    amount = c2b._amount(items.get("Amount"))
    if amount is None:
        problems.append("Amount could not be read.")
    paid_at = c2b._paid_at(items.get("TransactionDate"))
    if paid_at is None:
        paid_at = timezone.now()
        notes.append("Time could not be read; the time received is used.")
    raw_phone = str(items.get("PhoneNumber") or "")
    phone, hashed = c2b._msisdn(raw_phone)
    try:
        with transaction.atomic():
            return MpesaTransaction.objects.create(
                organization_id=stk.organization_id, payment_account_id=stk.payment_account_id,
                source=MpesaTransaction.Source.STK, trans_id=receipt, trans_type="STK",
                shortcode=creds.shortcode, bill_ref=stk.account_reference, amount=amount or 0, paid_at=paid_at,
                msisdn_raw=raw_phone[:80], msisdn_hash=hashed, payer_phone=phone, raw_payload=payload,
                status=MpesaTransaction.Status.FLAGGED if problems else MpesaTransaction.Status.RECEIVED,
                note=" ".join(problems + notes)[:300])
    except IntegrityError:
        # The C2B confirmation for the same receipt was stored at this moment.
        return MpesaTransaction.objects.select_for_update().get(trans_id=receipt)


def receive(creds: DarajaCredentials, payload: dict) -> StkRequest | None:
    """Records Safaricom's answer to a request. Repeats and unknown requests change nothing."""
    cb = _callback(payload)
    checkout = str(cb["CheckoutRequestID"])[:60]
    with transaction.atomic():
        stk = (StkRequest.objects.select_for_update()
               .filter(checkout_request_id=checkout, payment_account_id=creds.payment_account_id).first())
        if stk is None:
            logger.warning("STK callback for unknown request %s", checkout)
            return None
        if stk.status != Status.PENDING:
            return stk
        code, desc = str(cb.get("ResultCode", "")).strip(), str(cb.get("ResultDesc", "")).strip()
        if code != "0":
            _finish(stk, Status.FAILED, code, desc)
            return stk
        items = _items(cb)
        receipt = str(items.get("MpesaReceiptNumber") or "").strip().upper()[:20]
        if not receipt:
            # Paid, but nothing to record it by; the C2B confirmation or the statement will show it.
            stk.result_code, stk.result_desc = code, "Paid, but Safaricom sent no receipt number."
            stk.save(update_fields=["result_code", "result_desc", "updated_at"])
            return stk
        tx = _store(creds, stk, receipt, items, payload)
        if hasattr(tx, "stk_request") and tx.stk_request.pk != stk.pk:
            logger.error("M-Pesa receipt %s already belongs to another STK request", receipt)
            _finish(stk, Status.FAILED, code, f"Receipt {receipt} is already on another request.")
            return stk
        _finish(stk, Status.PAID, code, desc, tx)
    if tx.status == MpesaTransaction.Status.RECEIVED:
        c2b.safe_process(tx)
    elif tx.status == MpesaTransaction.Status.UNMATCHED:
        # The C2B confirmation came first and could not be placed; the request says where it goes.
        c2b.settle_stk(tx)
    return stk


# ---------------------------------------------------------------------------
# Asking Safaricom
# ---------------------------------------------------------------------------


def check_status(stk: StkRequest, *, actor: Membership | None = None) -> StkRequest:
    """Asks Safaricom what happened to a request whose callback has not come.

    A failure is recorded. Success is not enough to record the payment (the query has no receipt
    number), so the request stays PENDING until the callback or the C2B confirmation arrives.
    """
    if actor is not None:
        if stk.organization_id != actor.organization_id:
            raise PermissionDenied(_("That record belongs to another organization."))
        require(actor, "payments.record", stk.lease.unit.property)
    if stk.status != Status.PENDING or not stk.checkout_request_id:
        return stk
    creds = DarajaCredentials.objects.select_related("payment_account").get(payment_account=stk.payment_account)
    try:
        data = get_client(creds).stk_query(stk.checkout_request_id)
    except DarajaError as e:
        if STILL_PROCESSING in str(e):
            raise ValidationError(_("The tenant has not answered yet.")) from None
        raise ValidationError(_("Safaricom could not be asked: %(error)s") % {"error": e}) from None
    code, desc = str(data.get("ResultCode", "")).strip(), str(data.get("ResultDesc", "")).strip()
    with transaction.atomic():
        stk = StkRequest.objects.select_for_update().get(pk=stk.pk)
        if stk.status != Status.PENDING:
            return stk
        if code == "0":
            stk.result_code, stk.result_desc = code, "Paid; waiting for Safaricom's confirmation."
            stk.save(update_fields=["result_code", "result_desc", "updated_at"])
        elif code:
            _finish(stk, Status.FAILED, code, desc)
    return stk
