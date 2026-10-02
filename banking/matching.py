"""Matching bank lines to leases (D-064 item 5), by the rules M-Pesa uses (D-045).

1. Reference: a unit's payment reference appears in the description or reference, ignoring case
   and dashes, also split across two words ("GV A1"). Exactly one unit, served by this account,
   with exactly one active lease: confirmed at once.
2. Already recorded: the bank reference is on a live payment, or the lease has a live bank
   payment of the same amount within 3 days that came from no bank line. Left for staff.
3. Name: two words of a tenant's name on an active lease this account serves appear in the
   description. Only a suggestion.
"""

import datetime
import re

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from billing.invoicing import lease_balance
from leases.models import Lease, LeaseTenant
from mpesa import c2b
from payments import services as payment_services
from payments.models import Payment

from .models import BankTransaction

Status = BankTransaction.Status
SAME_PAYMENT_DAYS = 3
_WORD = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
_NAME_WORD = re.compile(r"[A-Z]{3,}")


def tokens(*texts: str) -> set[str]:
    """Words, normalized like payment references, and each pair of neighbouring words joined."""
    words = [c2b.normalize_reference(w) for text in texts for w in _WORD.findall(text or "")]
    found = set(words) | {a + b for a, b in zip(words, words[1:], strict=False)}
    return {t for t in found if t}


def match_reference(line: BankTransaction) -> tuple[Lease | None, str]:
    units = list(c2b.units_with_reference(line.organization_id, tokens(line.description, line.reference))[:2])
    if not units:
        return None, "No unit reference was found in the description."
    if len(units) > 1:
        return None, "The description has more than one unit reference."
    return c2b.lease_on_unit(units[0], line.payment_account_id)


def same_reference(line: BankTransaction) -> Payment | None:
    """A live payment that already carries this line's bank reference."""
    from payments.selectors import same_reference as payments_with

    if not line.reference:
        return None
    return payments_with(line.organization, line.reference).select_related("lease").first()


def same_payment(line: BankTransaction, lease: Lease) -> Payment | None:
    """A live bank payment on the lease, of the same amount and within a few days, that came from no bank line:
    most likely this money, typed in by hand."""
    days = datetime.timedelta(days=SAME_PAYMENT_DAYS)
    return (Payment.objects.filter(lease=lease, method=Payment.Method.BANK, amount=line.amount,
                                   paid_at__range=(line.posted_on - days, line.posted_on + days),
                                   bank_transaction__isnull=True)
            .exclude(status=Payment.Status.REVERSED).first())


def match_name(line: BankTransaction) -> Lease | None:
    """A lease to suggest from the payer's name in the description. Never confirms anything."""
    words = set(_NAME_WORD.findall(line.description.upper()))
    if len(words) < 2:
        return None
    ids = set()
    for lease_id, name in (LeaseTenant.objects.filter(lease__in=c2b._active_leases(line))
                           .values_list("lease_id", "tenant__name")):
        name_words = set(_NAME_WORD.findall((name or "").upper()))
        if len(name_words & words) >= 2:
            ids.add(lease_id)
    candidates = list(Lease.objects.filter(pk__in=ids))
    if len(candidates) > 1:
        candidates = [lease for lease in candidates if lease_balance(lease) == line.amount]
    return candidates[0] if len(candidates) == 1 else None


def confirm_to_lease(line: BankTransaction, lease: Lease, *, matched_by: str, user=None, actor=None) -> Payment:
    """Creates the confirmed bank payment for a line and marks it matched. `line` must be locked."""
    recorded = same_reference(line)
    if recorded is not None:
        raise ValidationError(f"The bank reference {line.reference} is already on a payment on "
                              f"{recorded.lease.number}; ignore this line instead of paying it twice.")
    source = f"bank:{line.public_id}" + (":manual" if actor else "")
    payment = payment_services.record_system_payment(
        lease, amount=line.amount, method=Payment.Method.BANK, paid_at=min(line.posted_on, timezone.localdate()),
        reference=line.reference or line.description[:60], source=source, payment_account=line.payment_account,
        actor=actor)
    line.status = Status.MATCHED
    line.payment = payment
    line.matched_by = matched_by
    line.matched_by_user = user
    line.matched_at = timezone.now()
    line.suggested_lease = None
    line.save()
    return payment


def _leave(line: BankTransaction, lease: Lease | None, note: str) -> None:
    line.status = Status.UNMATCHED
    line.suggested_lease = lease
    line.note = note.strip()[:300]
    line.save()


@transaction.atomic
def process(line: BankTransaction) -> BankTransaction:
    """Matches a new line: confirmed by reference, else left UNMATCHED, perhaps with a suggestion."""
    line = BankTransaction.objects.select_for_update().select_related("payment_account").get(pk=line.pk)
    if line.status != Status.UNMATCHED or line.matched_at is not None or line.payment_id:
        return line
    recorded = same_reference(line)
    if recorded is not None:
        _leave(line, recorded.lease, f"Reference {line.reference} is already recorded on {recorded.lease.number} "
                                     f"({recorded.get_status_display().lower()}); check it and ignore this line.")
        return line
    lease, reason = match_reference(line)
    if lease is not None:
        recorded = same_payment(line, lease)
        if recorded is not None:
            _leave(line, lease, f"A bank payment of the same amount is already recorded on {lease.number} "
                                f"({recorded.paid_at:%d %b %Y}); if it is this money, ignore this line.")
            return line
        confirm_to_lease(line, lease, matched_by=BankTransaction.MatchedBy.REFERENCE)
        return line
    suggestion = match_name(line)
    _leave(line, suggestion, f"{reason} The payer's name matches the suggested lease." if suggestion else reason)
    return line
