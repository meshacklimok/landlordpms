"""The bank inbox: statement lines the matching could not place (D-064 item 6).

Works like the M-Pesa inbox. Staff with `mpesa.match` accept the suggested lease, choose one, or
ignore the line with a reason. Matching creates a confirmed bank payment at once. A reversed bank
payment brings its line back.

Scope: an unmatched line is seen by members whose properties its account serves; an account that
serves no property in particular is seen only by members with every property. A matched line
follows its lease's property.
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

from . import matching
from .models import BankTransaction

Status = BankTransaction.Status


def visible_lines(membership: Membership, capability: str = "payments.view"):
    if not can(membership, capability):
        return BankTransaction.objects.none()
    qs = (BankTransaction.objects.filter(organization_id=membership.organization_id)
          .select_related("payment_account", "suggested_lease__unit__property", "payment__lease__unit__property",
                          "matched_by_user"))
    ids = accessible_property_ids(membership)
    if ids is None:
        return qs
    return qs.filter(Q(payment__isnull=True, payment_account__properties__property_id__in=ids)
                     | Q(payment__lease__unit__property_id__in=ids)).distinct()


def inbox(membership: Membership):
    return visible_lines(membership, "mpesa.match").filter(status=Status.UNMATCHED).order_by("posted_on", "pk")


def _locked(actor: Membership, line: BankTransaction) -> BankTransaction:
    if not visible_lines(actor, "mpesa.match").filter(pk=line.pk).exists():
        raise PermissionDenied(_("That record belongs to another organization."))
    return BankTransaction.objects.select_for_update().select_related("payment_account").get(pk=line.pk)


def _record(action: str, actor: Membership, line: BankTransaction, request, changes: dict) -> None:
    audit.record(action, actor=actor.user, organization=line.organization, obj=line, request=request,
                 changes={"line": [None, f"{line.posted_on} {line.amount} {line.label}"], **changes})


@transaction.atomic
def match(actor: Membership, line: BankTransaction, lease: Lease, *, request=None) -> Payment:
    if lease.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, "mpesa.match", lease.unit.property)
    if not visible_leases(actor, Lease.objects.filter(pk=lease.pk)).exists():
        raise PermissionDenied(_("That record belongs to another organization."))
    line = _locked(actor, line)
    if line.status != Status.UNMATCHED:
        raise ValidationError(_("Only an unmatched line can be matched."))
    suggested = line.suggested_lease_id == lease.pk
    payment = matching.confirm_to_lease(line, lease, matched_by=BankTransaction.MatchedBy.MANUAL, user=actor.user,
                                        actor=actor)
    _record("bank.match", actor, line, request, {"status": [Status.UNMATCHED, Status.MATCHED],
                                                  "lease": [None, lease.number], "suggested": [None, suggested]})
    return payment


def accept_suggestion(actor: Membership, line: BankTransaction, *, request=None) -> Payment:
    if line.suggested_lease_id is None:
        raise ValidationError(_("There is no suggested lease for this line."))
    return match(actor, line, line.suggested_lease, request=request)


@transaction.atomic
def ignore(actor: Membership, line: BankTransaction, *, reason: str, request=None) -> BankTransaction:
    """Takes a line out of the inbox, e.g. money that is not rent or is already recorded."""
    reason = (reason or "").strip()[:200]
    if not reason:
        raise ValidationError(_("Say why this line is being ignored."))
    line = _locked(actor, line)
    if line.status != Status.UNMATCHED:
        raise ValidationError(_("Only a line waiting in the inbox can be ignored."))
    line.status = Status.IGNORED
    line.note = f"{line.note} Ignored: {reason}".strip()[-300:]
    line.save(update_fields=["status", "note", "updated_at"])
    _record("bank.ignore", actor, line, request, {"status": [Status.UNMATCHED, Status.IGNORED],
                                                   "reason": [None, reason]})
    return line


@transaction.atomic
def restore(actor: Membership, line: BankTransaction, *, request=None) -> BankTransaction:
    line = _locked(actor, line)
    if line.status != Status.IGNORED:
        raise ValidationError(_("Only an ignored line can be put back."))
    line.status = Status.UNMATCHED
    line.save(update_fields=["status", "updated_at"])
    _record("bank.restore", actor, line, request, {"status": [Status.IGNORED, Status.UNMATCHED]})
    return line


def payment_reversed(payment: Payment, reason: str) -> BankTransaction | None:
    """Called inside `reverse_payment`: the line goes back to the inbox to be matched again."""
    line = BankTransaction.objects.select_for_update().filter(payment=payment).first()
    if line is None:
        return None
    line.status = Status.UNMATCHED
    line.payment = None
    line.matched_by = ""
    line.matched_by_user = None
    line.matched_at = None
    line.suggested_lease = None
    line.note = f"{line.note} Payment reversed: {reason}".strip()[-300:]
    line.save()
    return line
