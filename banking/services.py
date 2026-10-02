"""Bank accounts and statement imports (D-064 items 1, 2, 4 and 7).

Flow: upload -> preview (nothing stored but the preview) -> import. Importing a bank statement
adds its new lines and matches each; an M-Pesa statement adds the M-Pesa codes not yet received
and runs them through the same matching as a callback. A line that fails to match waits in its inbox.
"""

import datetime
import hashlib
import logging
from collections import Counter
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import accessible_property_ids, can, require, visible_properties
from audit import services as audit
from core.phone import InvalidPhoneNumber, normalize_phone
from mpesa import c2b
from mpesa.models import MpesaTransaction
from payments.models import PaymentAccount, PropertyPaymentAccount
from properties.models import Property

from . import matching, parsing
from .models import BankTransaction, StatementImport

logger = logging.getLogger(__name__)

Kind = StatementImport.Kind
Status = StatementImport.Status
Type = PaymentAccount.Type
PREVIEW_TTL = datetime.timedelta(hours=24)
KIND_FOR = {Type.BANK: Kind.BANK, Type.PAYBILL: Kind.MPESA, Type.TILL: Kind.MPESA}


# ---------------------------------------------------------------------------
# Bank accounts
# ---------------------------------------------------------------------------


def bank_accounts(org):
    return PaymentAccount.objects.filter(organization=org, type=Type.BANK).prefetch_related("properties__property")


@transaction.atomic
def add_bank_account(actor: Membership, *, bank: str, number: str, properties=(), request=None) -> PaymentAccount:
    """Adds a bank account rent is paid into, and the properties it collects for (none: all of them)."""
    require(actor, "payment_accounts.manage")
    bank = " ".join((bank or "").split())[:60]
    number = "".join((number or "").split())[:40]
    errors = {}
    if not bank:
        errors["bank"] = _("Name the bank.")
    if not number:
        errors["number"] = _("Enter the account number.")
    if errors:
        raise ValidationError(errors)
    org = actor.organization
    if PaymentAccount.all_objects.filter(organization=org, type=Type.BANK, number=number).exists():
        raise ValidationError({"number": _("This account is already added.")})
    properties = list(properties)
    if any(p.organization_id != org.pk for p in properties):
        raise PermissionDenied(_("That record belongs to another organization."))
    account = PaymentAccount.objects.create(organization=org, type=Type.BANK, number=number,
                                            display_name=f"{bank} {number[-4:]}"[:80])
    for prop in properties:
        PropertyPaymentAccount.objects.create(organization=org, property=prop, payment_account=account)
    audit.record("payment_account.create", actor=actor.user, organization=org, obj=account, request=request,
                 changes={"type": [None, Type.BANK], "number": [None, number],
                          "properties": [None, [p.code for p in properties]]})
    return account


# ---------------------------------------------------------------------------
# Who may import what
# ---------------------------------------------------------------------------


def importable_accounts(membership: Membership):
    """Bank, Paybill and Till accounts the member may import a statement for (D-064 item 2)."""
    if not can(membership, "mpesa.match"):
        return PaymentAccount.objects.none()
    qs = PaymentAccount.objects.filter(organization_id=membership.organization_id, type__in=list(KIND_FOR))
    ids = accessible_property_ids(membership)
    if ids is not None:
        qs = qs.filter(properties__property_id__in=ids).distinct()
    return qs.order_by("display_name")


def visible_imports(membership: Membership):
    accounts = importable_accounts(membership)
    return (StatementImport.objects.filter(organization_id=membership.organization_id,
                                           payment_account__in=accounts)
            .select_related("payment_account", "created_by"))


def _check(actor: Membership, account: PaymentAccount) -> None:
    if account.organization_id != actor.organization_id or not importable_accounts(actor).filter(
            pk=account.pk).exists():
        raise PermissionDenied(_("That record belongs to another organization."))


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def fingerprint(account_id: int, row: dict, occurrence: int) -> str:
    """The same line in an overlapping statement gives the same fingerprint (D-064 item 4)."""
    parts = [str(account_id), row["posted_on"], row["amount"], " ".join(row["description"].upper().split()),
             row["reference"].upper(), row["balance"] or "", str(occurrence)]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _bank_rows(account: PaymentAccount, parsed: parsing.Parsed) -> tuple[list[dict], int]:
    seen: Counter = Counter()
    rows = []
    for line in parsed.lines:
        row = {"line": line.line, "posted_on": line.posted_on.isoformat(), "description": line.description,
               "reference": line.reference, "amount": str(line.amount),
               "balance": None if line.balance is None else str(line.balance)}
        key = (row["posted_on"], row["amount"], row["description"].upper(), row["reference"].upper(), row["balance"])
        seen[key] += 1
        row["fingerprint"] = fingerprint(account.pk, row, seen[key])
        rows.append(row)
    known = set(BankTransaction.objects.filter(payment_account=account,
                                               fingerprint__in=[r["fingerprint"] for r in rows])
                .values_list("fingerprint", flat=True))
    return [r for r in rows if r["fingerprint"] not in known], len(known)


def _mpesa_rows(parsed: parsing.Parsed) -> tuple[list[dict], int]:
    rows, codes = [], set()
    for line in parsed.lines:
        if line.receipt in codes:
            continue
        codes.add(line.receipt)
        rows.append({"line": line.line, "receipt": line.receipt, "paid_at": line.paid_at.isoformat(),
                     "amount": str(line.amount), "bill_ref": line.bill_ref, "details": line.details,
                     "payer_raw": line.payer_raw, "payer_name": line.payer_name})
    known = set(MpesaTransaction.objects.filter(trans_id__in=codes).values_list("trans_id", flat=True))
    return [r for r in rows if r["receipt"] not in known], len(parsed.lines) - len(rows) + len(known)


def preview(actor: Membership, account: PaymentAccount, upload, *, request=None) -> StatementImport:
    """Reads the file and stores what importing it would add. Raises ValidationError if it cannot be read."""
    _check(actor, account)
    kind = KIND_FOR[account.type]
    if upload.size > parsing.MAX_BYTES:
        raise ValidationError(_("The file is larger than 1 MB."))
    try:
        parsed = (parsing.read_bank if kind == Kind.BANK else parsing.read_mpesa)(upload.read())
    except parsing.StatementError as e:
        raise ValidationError(str(e)) from None
    rows, duplicates = _bank_rows(account, parsed) if kind == Kind.BANK else _mpesa_rows(parsed)
    dates = [line.posted_on if kind == Kind.BANK else line.paid_at.astimezone(parsing.NAIROBI).date()
             for line in parsed.lines]
    batch = StatementImport.objects.create(
        organization=actor.organization, payment_account=account, kind=kind, created_by=actor.user,
        file_name=(upload.name or "")[:200], rows=rows, errors=[list(e) for e in parsed.errors[:200]],
        new_count=len(rows), duplicate_count=duplicates, skipped_count=parsed.skipped,
        period_from=min(dates, default=None), period_to=max(dates, default=None))
    audit.record("statement.preview", actor=actor.user, organization=actor.organization, obj=batch, request=request,
                 changes={"account": [None, account.display_name], "new": [None, len(rows)],
                          "duplicates": [None, duplicates], "errors": [None, len(parsed.errors)]})
    return batch


def is_expired(batch: StatementImport) -> bool:
    return batch.status == Status.PREVIEW and timezone.now() >= batch.created_at + PREVIEW_TTL


def purge_stale_previews() -> int:
    return StatementImport.objects.filter(status=Status.PREVIEW, created_at__lt=timezone.now() - PREVIEW_TTL).update(
        status=Status.DISCARDED, rows=[], updated_at=timezone.now())


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _locked(actor: Membership, batch: StatementImport) -> StatementImport:
    if not visible_imports(actor).filter(pk=batch.pk).exists():
        raise PermissionDenied(_("That record belongs to another organization."))
    batch = StatementImport.objects.select_for_update().select_related("payment_account").get(pk=batch.pk)
    if batch.status != Status.PREVIEW:
        raise ValidationError(_("This statement has already been imported or discarded."))
    if is_expired(batch):
        raise ValidationError(_("This preview has expired. Upload the file again."))
    return batch


def _add_bank_line(batch: StatementImport, row: dict) -> BankTransaction | None:
    try:
        with transaction.atomic():
            return BankTransaction.objects.create(
                organization_id=batch.organization_id, payment_account=batch.payment_account, statement=batch,
                posted_on=datetime.date.fromisoformat(row["posted_on"]), description=row["description"],
                reference=row["reference"], amount=Decimal(row["amount"]),
                balance=None if row["balance"] is None else Decimal(row["balance"]), fingerprint=row["fingerprint"])
    except IntegrityError:
        return None  # imported meanwhile from another statement


def _payer(raw: str) -> tuple[str, str]:
    if "*" in raw or not raw:
        return "", ""
    try:
        phone = normalize_phone(raw)
    except InvalidPhoneNumber:
        return "", ""
    return phone, c2b.phone_hash(phone)


def _add_mpesa(batch: StatementImport, row: dict) -> MpesaTransaction | None:
    phone, hashed = _payer(row["payer_raw"])
    try:
        with transaction.atomic():
            return MpesaTransaction.objects.create(
                organization_id=batch.organization_id, payment_account=batch.payment_account,
                source=MpesaTransaction.Source.STATEMENT, trans_id=row["receipt"],
                bill_ref=row["bill_ref"], amount=Decimal(row["amount"]),
                paid_at=datetime.datetime.fromisoformat(row["paid_at"]), msisdn_raw=row["payer_raw"],
                msisdn_hash=hashed, payer_phone=phone, payer_name=row["payer_name"],
                raw_payload={"statement": str(batch.public_id), **row}, note="From a statement import.")
    except IntegrityError:
        return None  # the callback, or another import, got there first


def apply(actor: Membership, batch: StatementImport, *, request=None) -> StatementImport:
    """Adds the new lines and matches each. Lines that fail to match wait in the inbox."""
    with transaction.atomic():
        batch = _locked(actor, batch)
        rows = batch.rows
        added = []
        add = _add_bank_line if batch.kind == Kind.BANK else _add_mpesa
        for row in rows:
            obj = add(batch, row)
            if obj is not None:
                added.append(obj)
        batch.status = Status.APPLIED
        batch.applied_at = timezone.now()
        batch.rows = []
        batch.duplicate_count += len(rows) - len(added)
        batch.new_count = len(added)
        batch.save()
    matched = 0
    for obj in added:
        if batch.kind == Kind.BANK:
            try:
                matched += matching.process(obj).status == BankTransaction.Status.MATCHED
            except Exception:
                logger.exception("Bank line %s could not be matched", obj.pk)
                BankTransaction.objects.filter(pk=obj.pk, status=BankTransaction.Status.UNMATCHED).update(
                    note="It could not be matched automatically; match it by hand.")
        else:
            c2b.safe_process(obj)
            obj.refresh_from_db(fields=["status"])
            matched += obj.status == MpesaTransaction.Status.MATCHED
    StatementImport.objects.filter(pk=batch.pk).update(matched_count=matched, unmatched_count=len(added) - matched)
    batch.refresh_from_db()
    audit.record("statement.import", actor=actor.user, organization=actor.organization, obj=batch, request=request,
                 changes={"account": [None, batch.payment_account.display_name], "added": [None, len(added)],
                          "matched": [None, matched]})
    return batch


@transaction.atomic
def discard(actor: Membership, batch: StatementImport) -> StatementImport:
    batch = _locked(actor, batch)
    batch.status = Status.DISCARDED
    batch.rows = []
    batch.save(update_fields=["status", "rows", "updated_at"])
    return batch


def account_properties(membership: Membership):
    return visible_properties(membership, Property.objects.all())
