"""The M-Pesa inbox: payments the matching engine could not place (D-045 item 7).

Staff with `mpesa.match` accept the suggested lease, choose a lease by hand, or ignore the
transaction with a reason. Accepting or matching creates a confirmed payment at once (the money
is known to have arrived). A reversed M-Pesa payment comes back here to be matched again.

Scope: an unmatched transaction belongs to no property yet, so a member sees it when its account
collects for one of their properties; an account that serves no property in particular is seen
only by members with every property. A matched transaction follows its lease's property.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import accessible_property_ids, can, require
from audit import services as audit
from leases.models import Lease
from leases.services import visible_leases
from payments.models import Payment

from . import c2b
from .models import MpesaTransaction

Status = MpesaTransaction.Status
OPEN = (Status.UNMATCHED, Status.FLAGGED)


def visible_transactions(membership: Membership, capability: str = "mpesa.view_transactions"):
    if not can(membership, capability):
        return MpesaTransaction.objects.none()
    qs = (MpesaTransaction.objects.filter(organization_id=membership.organization_id)
          .select_related("payment_account", "suggested_lease__unit__property", "payment__lease__unit__property",
                          "matched_by_user"))
    ids = accessible_property_ids(membership)
    if ids is None:
        return qs
    return qs.filter(Q(payment__isnull=True, payment_account__properties__property_id__in=ids)
                     | Q(payment__lease__unit__property_id__in=ids)).distinct()


def inbox(membership: Membership):
    """Waiting for a person: UNMATCHED, and FLAGGED (unreadable or sent to the wrong shortcode)."""
    return visible_transactions(membership, "mpesa.match").filter(status__in=OPEN).order_by("paid_at", "pk")


def _locked(actor: Membership, tx: MpesaTransaction) -> MpesaTransaction:
    """The transaction, locked, if the actor may work on it; a 404-style refusal otherwise."""
    if not visible_transactions(actor, "mpesa.match").filter(pk=tx.pk).exists():
        raise PermissionDenied(_("That record belongs to another organization."))
    return MpesaTransaction.objects.select_for_update().select_related("payment_account").get(pk=tx.pk)


def _record(action: str, actor: Membership, tx: MpesaTransaction, request, changes: dict) -> None:
    audit.record(action, actor=actor.user, organization=tx.organization, obj=tx, request=request,
                 changes={"trans_id": [None, tx.trans_id], **changes})


@transaction.atomic
def match(actor: Membership, tx: MpesaTransaction, lease: Lease, *, request=None) -> Payment:
    """Places an unmatched transaction on a lease: a confirmed payment, allocated and receipted."""
    if lease.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, "mpesa.match", lease.unit.property)
    if not visible_leases(actor, Lease.objects.filter(pk=lease.pk)).exists():
        raise PermissionDenied(_("That record belongs to another organization."))
    tx = _locked(actor, tx)
    if tx.status != Status.UNMATCHED:
        raise ValidationError(_("Only an unmatched payment can be matched."))
    matched_by = MpesaTransaction.MatchedBy.MANUAL
    suggested = tx.suggested_lease_id == lease.pk
    payment = c2b.confirm_to_lease(tx, lease, matched_by=matched_by, user=actor.user, actor=actor,
                                   source=f"mpesa:{tx.trans_id}:manual")
    _record("mpesa.match", actor, tx, request, {"status": [Status.UNMATCHED, Status.MATCHED],
                                                  "lease": [None, lease.number],
                                                  "suggested": [None, suggested]})
    return payment


def accept_suggestion(actor: Membership, tx: MpesaTransaction, *, request=None) -> Payment:
    if tx.suggested_lease_id is None:
        raise ValidationError(_("There is no suggested lease for this payment."))
    return match(actor, tx, tx.suggested_lease, request=request)


def _reason(reason: str) -> str:
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(_("Say why this payment is being ignored."))
    return reason[:200]


@transaction.atomic
def ignore(actor: Membership, tx: MpesaTransaction, *, reason: str, request=None) -> MpesaTransaction:
    """Takes a transaction out of the inbox, e.g. money that is not rent or was refunded."""
    reason = _reason(reason)
    tx = _locked(actor, tx)
    if tx.status not in OPEN:
        raise ValidationError(_("Only a payment waiting in the inbox can be ignored."))
    before = tx.status
    tx.status = Status.IGNORED
    tx.note = f"{tx.note} Ignored: {reason}".strip()[:300]
    tx.save(update_fields=["status", "note", "updated_at"])
    _record("mpesa.ignore", actor, tx, request, {"status": [before, Status.IGNORED], "reason": [None, reason]})
    return tx


def _open_status(tx: MpesaTransaction) -> str:
    creds = getattr(tx.payment_account, "daraja", None)
    wrong_shortcode = creds is not None and tx.shortcode and tx.shortcode != creds.shortcode
    return Status.FLAGGED if tx.amount <= 0 or wrong_shortcode else Status.UNMATCHED


@transaction.atomic
def restore(actor: Membership, tx: MpesaTransaction, *, request=None) -> MpesaTransaction:
    """Puts an ignored transaction back in the inbox."""
    tx = _locked(actor, tx)
    if tx.status != Status.IGNORED:
        raise ValidationError(_("Only an ignored payment can be put back."))
    tx.status = _open_status(tx)
    tx.save(update_fields=["status", "updated_at"])
    _record("mpesa.restore", actor, tx, request, {"status": [Status.IGNORED, tx.status]})
    return tx


def payment_reversed(payment: Payment, reason: str) -> MpesaTransaction | None:
    """Called inside `reverse_payment`: the transaction goes back to the inbox to be matched again."""
    tx = MpesaTransaction.objects.select_for_update().filter(payment=payment).first()
    if tx is None:
        return None
    tx.status = Status.UNMATCHED
    tx.payment = None
    tx.matched_by = ""
    tx.matched_by_user = None
    tx.matched_at = None
    tx.suggested_lease = None  # the lease it was on is the one it was taken off
    tx.note = f"{tx.note} Payment reversed: {reason}".strip()[:300]
    tx.save()
    return tx
