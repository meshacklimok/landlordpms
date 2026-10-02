"""M-Pesa codes typed in by hand that nothing from Safaricom confirmed (D-066).

A payment recorded from the code in a tenant's SMS is proven once a callback or an imported
statement brings the same code in. Until then it is only someone's word. After 24 hours, if a
callback or a statement should have brought it and did not, the payment is flagged "not found".
A code that came in with a different amount is flagged at once. Staff reverse the payment, or
mark the code checked with a note.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership, Organization
from accounts.permissions import can, require
from audit import services as audit
from banking.models import StatementImport
from payments import selectors as payment_selectors
from payments.models import Payment, PaymentAccount

from .models import CodeCheck, DarajaCredentials, MpesaTransaction

WAIT = datetime.timedelta(hours=24)
NOT_FOUND = "missing"
AMOUNT_DIFFERS = "amount"
MPESA_ACCOUNTS = (PaymentAccount.Type.PAYBILL, PaymentAccount.Type.TILL)


@dataclass
class Flagged:
    payment: Payment
    reason: str
    # The amount Safaricom reported for the code, when the code was found with another amount.
    found_amount: Decimal | None = None

    @property
    def amount_differs(self) -> bool:
        return self.reason == AMOUNT_DIFFERS


@dataclass
class Review:
    flagged: list[Flagged] = field(default_factory=list)
    # Recorded less than 24 hours ago: the callback or statement may still come.
    waiting: int = 0
    # Nothing would bring the code in: no C2B registration and no statement covering the date paid.
    cannot_check: int = 0


def unproven(payments):
    """Live M-Pesa payments with a code that are not the payment of a transaction and not checked by hand."""
    found = MpesaTransaction.objects.filter(organization_id=OuterRef("organization_id"),
                                            trans_id__iexact=OuterRef("reference"))
    return (payments.filter(method=Payment.Method.MPESA,
                            status__in=[Payment.Status.PENDING_REVIEW, Payment.Status.CONFIRMED],
                            mpesa_transaction__isnull=True, code_check__isnull=True)
            .exclude(reference="")
            .annotate(found_amount=Subquery(found.values("amount")[:1])))


class _Sources:
    """What should bring a code in, per organization and account."""

    def __init__(self, org_ids):
        self.registered: dict[int, datetime.date] = {}
        for account_id, at in (DarajaCredentials.objects.filter(organization_id__in=org_ids,
                                                                urls_registered_at__isnull=False)
                               .values_list("payment_account_id", "urls_registered_at")):
            self.registered[account_id] = timezone.localdate(at)
        self.periods: dict[int, list[tuple[datetime.date, datetime.date]]] = defaultdict(list)
        for account_id, start, end in (StatementImport.objects.filter(
                organization_id__in=org_ids, kind=StatementImport.Kind.MPESA, status=StatementImport.Status.APPLIED,
                period_from__isnull=False, period_to__isnull=False)
                .values_list("payment_account_id", "period_from", "period_to")):
            self.periods[account_id].append((start, end))
        self.accounts: dict[int, list[int]] = defaultdict(list)
        for account_id, org_id in (PaymentAccount.objects.filter(organization_id__in=org_ids, type__in=MPESA_ACCOUNTS)
                                   .values_list("pk", "organization_id")):
            self.accounts[org_id].append(account_id)

    def covers(self, payment: Payment) -> bool:
        accounts = ([payment.payment_account_id] if payment.payment_account_id
                    else self.accounts[payment.organization_id])
        day = payment.paid_at
        for account_id in accounts:
            registered = self.registered.get(account_id)
            if registered is not None and registered <= day:
                return True
            if any(start <= day <= end for start, end in self.periods.get(account_id, ())):
                return True
        return False


def review(payments, now: datetime.datetime | None = None) -> Review:
    """Sorts the unproven payments among `payments` into flagged, waiting and cannot be checked."""
    now = now or timezone.now()
    rows = list(unproven(payments).select_related("lease__unit__property", "tenant", "recorded_by")
                .order_by("paid_at", "pk"))
    result = Review()
    if not rows:
        return result
    sources = _Sources({p.organization_id for p in rows})
    for p in rows:
        if p.found_amount is not None:
            if p.found_amount != p.amount:
                result.flagged.append(Flagged(p, AMOUNT_DIFFERS, p.found_amount))
            # Same amount: the code is proven; `mpesa_transaction` stays empty because the
            # transaction was ignored as a duplicate of this payment (D-045 item 12).
        elif p.created_at > now - WAIT:
            result.waiting += 1
        elif sources.covers(p):
            result.flagged.append(Flagged(p, NOT_FOUND))
        else:
            result.cannot_check += 1
    return result


def for_member(membership: Membership):
    """The payments a member may check: those they can see, if they may match M-Pesa payments."""
    if not can(membership, "mpesa.match"):
        return Payment.objects.none()
    return payment_selectors.visible_payments(membership)


def flag_for(payment: Payment, now: datetime.datetime | None = None) -> Flagged | None:
    flagged = review(Payment.objects.filter(pk=payment.pk), now).flagged
    return flagged[0] if flagged else None


def mark_checked(actor: Membership, payment: Payment, *, note: str, request=None) -> CodeCheck:
    if payment.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, "mpesa.match", payment.lease.unit.property)
    note = " ".join((note or "").split())[:300]
    if not note:
        raise ValidationError(_("Say how the code was checked."))
    with transaction.atomic():
        Payment.objects.select_for_update().filter(pk=payment.pk).exists()
        if not unproven(Payment.objects.filter(pk=payment.pk)).exists():
            raise ValidationError(_("This payment has nothing to check."))
        try:
            with transaction.atomic():
                check = CodeCheck.objects.create(organization_id=payment.organization_id, payment=payment,
                                                 checked_by=actor.user, note=note)
        except IntegrityError:
            raise ValidationError(_("This code was already marked as checked.")) from None
        audit.record("mpesa.code_checked", actor=actor.user, organization=actor.organization, obj=payment,
                     request=request, changes={"reference": [None, payment.reference], "note": [None, note]})
    return check


def send_alerts(now: datetime.datetime) -> int:
    """Once a day, in-app, to each `mpesa.match` holder who has codes to check."""
    from notifications.delivery import notify

    day = timezone.localdate(now)
    sent = 0
    orgs = Organization.objects.filter(pk__in=unproven(Payment.objects.all()).values("organization_id"))
    for org in orgs:
        members = (Membership.objects.filter(organization=org, is_active=True, user__is_active=True)
                   .select_related("user", "organization", "role"))
        for m in members:
            count = len(review(for_member(m), now).flagged)
            if count and notify(org, "mpesa_unverified_codes", user=m.user, context={"count": count},
                                dedupe_key=f"mpesa_codes:{day.isoformat()}:{m.user.pk}"):
                sent += 1
    return sent
