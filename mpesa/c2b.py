"""Receiving and matching M-Pesa payments (D-045 items 4-6).

`receive` stores the callback as it came, once per M-Pesa code, then `process` matches it:

1. Reference: the account number typed, ignoring case, spaces and dashes, is one unit's payment
   reference; that unit has exactly one active lease; and this account serves the unit's
   property (or no property in particular). Confident: the payment is confirmed at once.
2. Phone: the payer's number, or its hash, is on exactly one active lease (a tenant or an extra
   payer); among several, the one whose balance equals the amount. Only a suggestion.
3. Otherwise the transaction waits in the inbox as UNMATCHED.

A payment made in answer to a payment request (STK push) skips all this: the request named the
lease, so it is confirmed there (D-045 item 8).
"""

import datetime
import hashlib
import logging
import re
import zoneinfo
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Value
from django.db.models.functions import Replace, Upper
from django.utils import timezone

from billing.invoicing import lease_balance
from core.phone import InvalidPhoneNumber, normalize_phone
from leases.models import Lease, LeasePayer, LeaseTenant
from payments import services as payment_services
from payments.models import Payment, PropertyPaymentAccount
from properties.models import Unit
from tenants.models import Tenant

from .models import DarajaCredentials, MpesaTransaction

logger = logging.getLogger(__name__)

Status = MpesaTransaction.Status
NAIROBI = zoneinfo.ZoneInfo("Africa/Nairobi")
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
_CENT = Decimal("0.01")
RETRY_NOTE = "Processing failed; it will be retried."


class BadCallback(ValueError):
    """The callback has no M-Pesa code, so it cannot be stored."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def phone_hash(e164: str) -> str:
    """SHA-256 of the number as 2547XXXXXXXX, the form Daraja hashes. [VERIFY in the sandbox]"""
    return hashlib.sha256(e164.lstrip("+").encode()).hexdigest()


def _msisdn(raw: str) -> tuple[str, str]:
    """(E.164 phone or "", hash or "") from MSISDN, which may already be a hash."""
    raw = (raw or "").strip()
    if _HEX64.match(raw):
        return "", raw.lower()
    try:
        phone = normalize_phone(raw)
    except InvalidPhoneNumber:
        return "", ""
    return phone, phone_hash(phone)


def _amount(raw) -> Decimal | None:
    try:
        value = Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not value.is_finite() or value <= 0:
        return None
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _paid_at(raw) -> datetime.datetime | None:
    """TransTime is YYYYMMDDHHMMSS in Nairobi time."""
    try:
        naive = datetime.datetime.strptime(str(raw).strip(), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=NAIROBI)


def normalize_reference(value: str) -> str:
    return re.sub(r"[\s\-]", "", value or "").upper()


def _text(payload: dict, key: str, limit: int) -> str:
    value = payload.get(key)
    return "" if value is None else str(value).strip()[:limit]


# ---------------------------------------------------------------------------
# Receiving
# ---------------------------------------------------------------------------


def receive(creds: DarajaCredentials, payload: dict) -> tuple[MpesaTransaction, bool]:
    """Stores a C2B confirmation, then matches it. Returns (transaction, created).

    A repeated callback for the same M-Pesa code returns the stored row untouched.
    """
    trans_id = _text(payload, "TransID", 20).upper()
    if not trans_id:
        raise BadCallback("TransID missing")
    existing = MpesaTransaction.objects.filter(trans_id=trans_id).first()
    if existing:
        return existing, False

    # Problems that stop a transaction being matched (FLAGGED), then ones that only need a note.
    problems, notes = [], []
    amount = _amount(payload.get("TransAmount"))
    if amount is None:
        problems.append("Amount could not be read.")
    shortcode = _text(payload, "BusinessShortCode", 10)
    if shortcode and shortcode != creds.shortcode:
        problems.append(f"Sent to shortcode {shortcode}, but this account is {creds.shortcode}.")
    paid_at = _paid_at(payload.get("TransTime"))
    if paid_at is None:
        paid_at = timezone.now()
        notes.append("Time could not be read; the time received is used.")
    msisdn_raw = _text(payload, "MSISDN", 80)
    phone, hashed = _msisdn(msisdn_raw)
    name = " ".join(_text(payload, k, 50) for k in ("FirstName", "MiddleName", "LastName")).split()

    try:
        with transaction.atomic():
            tx = MpesaTransaction.objects.create(
                organization_id=creds.organization_id, payment_account_id=creds.payment_account_id,
                source=MpesaTransaction.Source.C2B, trans_id=trans_id,
                trans_type=_text(payload, "TransactionType", 40), shortcode=shortcode,
                bill_ref=_text(payload, "BillRefNumber", 60), amount=amount or 0, paid_at=paid_at,
                msisdn_raw=msisdn_raw, msisdn_hash=hashed, payer_phone=phone, payer_name=" ".join(name)[:150],
                raw_payload=payload,
                status=Status.FLAGGED if problems else Status.RECEIVED, note=" ".join(problems + notes)[:300])
    except IntegrityError:
        # The same callback arrived twice at once; the other request stored it.
        return MpesaTransaction.objects.get(trans_id=trans_id), False
    safe_process(tx)
    tx.refresh_from_db()
    return tx, True


def safe_process(tx: MpesaTransaction) -> None:
    """Processes a stored transaction; a failure leaves it RECEIVED to be retried."""
    try:
        process(tx)
    except Exception:
        logger.exception("M-Pesa transaction %s could not be processed", tx.trans_id)
        note = tx.note if RETRY_NOTE in tx.note else f"{tx.note} {RETRY_NOTE}".strip()
        MpesaTransaction.objects.filter(pk=tx.pk, status=Status.RECEIVED).update(
            attempts=F("attempts") + 1, note=note[:300])


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _serves(account_id: int, property_id: int) -> bool:
    served = PropertyPaymentAccount.objects.filter(payment_account_id=account_id)
    return not served.exists() or served.filter(property_id=property_id).exists()


def _active_leases(tx: MpesaTransaction):
    leases = Lease.objects.filter(organization_id=tx.organization_id, status=Lease.Status.ACTIVE)
    served = PropertyPaymentAccount.objects.filter(payment_account_id=tx.payment_account_id)
    if served.exists():
        leases = leases.filter(unit__property_id__in=served.values("property_id"))
    return leases


def match_reference(tx: MpesaTransaction) -> tuple[Lease | None, str]:
    """(the one active lease on the unit whose reference was typed, or None with the reason)."""
    ref = normalize_reference(tx.bill_ref)
    if not ref:
        return None, "No account reference was given."
    units = list(Unit.objects.filter(organization_id=tx.organization_id)
                 .annotate(ref=Replace(Upper("payment_reference"), Value("-"), Value("")))
                 .filter(ref=ref).select_related("property")[:2])
    if len(units) != 1:
        return None, ("No unit has this account reference." if not units
                      else "Several units have this account reference.")
    unit = units[0]
    if not _serves(tx.payment_account_id, unit.property_id):
        return None, f"The reference is {unit}, but this account does not collect for {unit.property.name}."
    leases = list(Lease.objects.filter(unit=unit, status=Lease.Status.ACTIVE)[:2])
    if len(leases) != 1:
        return None, (f"The reference is {unit}, which has no active lease." if not leases
                      else f"The reference is {unit}, which has more than one active lease.")
    return leases[0], ""


def _phones_by_lease(leases) -> dict[int, dict[str, int | None]]:
    """{lease id: {phone hash: tenant id, or None for an extra payer}} for the given leases."""
    found: dict[int, dict[str, int | None]] = {}
    for lease_id, tenant_id, phone, alt in (LeaseTenant.objects.filter(lease__in=leases)
                                            .values_list("lease_id", "tenant_id", "tenant__phone",
                                                         "tenant__alt_phone")):
        for number in (phone, alt):
            if number:
                found.setdefault(lease_id, {}).setdefault(phone_hash(number), tenant_id)
    for lease_id, number in LeasePayer.objects.filter(lease__in=leases).values_list("lease_id", "phone"):
        found.setdefault(lease_id, {}).setdefault(phone_hash(number), None)
    return found


def paying_tenant(tx: MpesaTransaction, lease: Lease):
    """The tenant on the lease whose number paid, if any."""
    if not tx.msisdn_hash:
        return None
    tenant_id = _phones_by_lease([lease]).get(lease.pk, {}).get(tx.msisdn_hash)
    return Tenant.objects.get(pk=tenant_id) if tenant_id else None


def match_phone(tx: MpesaTransaction) -> Lease | None:
    """A lease to suggest from the payer's number. Never confirms anything."""
    if not tx.msisdn_hash:
        return None
    phones = _phones_by_lease(_active_leases(tx))
    ids = [lease_id for lease_id, hashes in phones.items() if tx.msisdn_hash in hashes]
    candidates = list(Lease.objects.filter(pk__in=ids))
    if len(candidates) > 1:
        candidates = [lease for lease in candidates if lease_balance(lease) == tx.amount]
    return candidates[0] if len(candidates) == 1 else None


def confirm_to_lease(tx: MpesaTransaction, lease: Lease, *, matched_by: str, user=None, actor=None,
                     source: str) -> Payment:
    """Creates the confirmed payment for a transaction and marks it matched. `tx` must be locked.

    `actor` is the member matching by hand; None for an automatic match.
    """
    recorded = recorded_by_hand(tx)
    if recorded is not None:
        raise ValidationError(f"This M-Pesa code was already recorded by hand on {recorded.lease.number}; "
                              "ignore this transaction instead of paying it twice.")
    paid_on = min(timezone.localdate(tx.paid_at), timezone.localdate())
    payment = payment_services.record_system_payment(
        lease, amount=tx.amount, method=Payment.Method.MPESA, paid_at=paid_on, reference=tx.trans_id,
        source=source, payment_account=tx.payment_account, tenant=paying_tenant(tx, lease),
        actor=actor)
    tx.status = Status.MATCHED
    tx.payment = payment
    tx.matched_by = matched_by
    tx.matched_by_user = user
    tx.matched_at = timezone.now()
    tx.suggested_lease = None
    tx.save()
    return payment


def recorded_by_hand(tx: MpesaTransaction) -> Payment | None:
    """A live payment someone typed in with this transaction's M-Pesa code, for example from the
    tenant's SMS before Safaricom's confirmation came. Paying the transaction too would count the
    money twice."""
    from payments.selectors import same_reference

    return same_reference(tx.organization, tx.trans_id).select_related("lease").first()


@transaction.atomic
def process(tx: MpesaTransaction) -> MpesaTransaction:
    """Matches a RECEIVED transaction: confirmed by reference, else left UNMATCHED (with a suggestion)."""
    tx = MpesaTransaction.objects.select_for_update().select_related("payment_account").get(pk=tx.pk)
    if tx.status != Status.RECEIVED:
        return tx
    tx.attempts += 1
    tx.processed_at = timezone.now()
    tx.note = tx.note.replace(RETRY_NOTE, "").strip()
    recorded = recorded_by_hand(tx)
    if recorded is not None:
        # Staff check the typed-in payment and ignore this one; the payer already has a receipt,
        # so only staff are told.
        tx.status = Status.UNMATCHED
        tx.suggested_lease = recorded.lease
        tx.note = (f"{tx.note} Already recorded by hand on {recorded.lease.number} "
                   f"({recorded.get_status_display().lower()}); check it and ignore this one.").strip()[:300]
        tx.save()
        _alert(tx, payer=False)
        return tx
    stk_reason = _confirm_stk(tx)
    if tx.status == Status.MATCHED:
        return tx
    # A request that could not take the payment is not overruled by the account number typed.
    lease, reason = (None, stk_reason) if stk_reason else match_reference(tx)
    if lease is not None:
        confirm_to_lease(tx, lease, matched_by=MpesaTransaction.MatchedBy.REFERENCE,
                         source=f"mpesa:{tx.trans_id}")
        return tx
    suggestion = match_phone(tx)
    tx.status = Status.UNMATCHED
    tx.suggested_lease = suggestion
    reason = f"{reason} The payer's number is on the suggested lease." if suggestion else reason
    tx.note = f"{tx.note} {reason}".strip()[:300]
    tx.save()
    _alert(tx)
    return tx


def _alert(tx: MpesaTransaction, *, payer: bool = True) -> None:
    """Tells the staff who can match it, and the payer. A failure here never undoes the matching."""
    from notifications import triggers

    try:
        with transaction.atomic():
            triggers.mpesa_unmatched(tx)
            if payer:
                triggers.payer_unmatched(tx)
    except Exception:
        logger.exception("Alerts for unmatched M-Pesa transaction %s failed", tx.trans_id)


def _stk_request(tx: MpesaTransaction):
    from .models import StkRequest

    return StkRequest.objects.filter(transaction=tx).select_related("lease").first()


def _confirm_stk(tx: MpesaTransaction) -> str:
    """Confirms a locked transaction on the lease its payment request named. Returns why it could
    not ("" when it was confirmed, or when there was no request)."""
    stk = _stk_request(tx)
    if stk is None:
        return ""
    try:
        with transaction.atomic():
            confirm_to_lease(tx, stk.lease, matched_by=MpesaTransaction.MatchedBy.STK,
                             source=f"mpesa:{tx.trans_id}:stk")
    except ValidationError as e:
        return f"Paid in answer to a request on {stk.lease.number}, which cannot take it: {' '.join(e.messages)}"
    return ""


def settle_stk(tx: MpesaTransaction) -> None:
    """Confirms an UNMATCHED transaction that turned out to answer a payment request."""
    try:
        with transaction.atomic():
            tx = MpesaTransaction.objects.select_for_update().select_related("payment_account").get(pk=tx.pk)
            if tx.status != Status.UNMATCHED:
                return
            reason = _confirm_stk(tx)
            if reason:
                tx.status = Status.UNMATCHED
                tx.note = f"{tx.note} {reason}".strip()[:300]
                tx.save()
    except Exception:
        logger.exception("M-Pesa transaction %s could not be placed on its payment request", tx.trans_id)
