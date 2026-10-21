"""The owner desk (D-217): messages, approvals and payouts between a property owner and the office.

Owner side functions take the portal `OwnerAccount`; office side functions take a `Membership` and use the same
check as inviting (`owners.invite_portal`, and the member must see every one of the owner's properties).
Every step is audited. Approvals and payout requests are records: they move no money and change no expense.
"""

import re

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from audit import services as audit
from core import ratelimit
from core.money import format_money
from expenses.models import Expense
from maintenance.models import MaintenanceRequest
from notifications import triggers
from properties.models import PropertyOwner, owned_by

from . import services
from .models import OwnerAccount, OwnerApproval, OwnerMessage, OwnerPayoutDetail, OwnerPayoutRequest

PER_HOUR = 10
SHOWN = 100
PHONE = re.compile(r"^\+?\d{9,15}$")


def _clean(body: str, limit=OwnerMessage.MAX_LENGTH) -> str:
    body = (body or "").strip()
    if not body:
        raise ValidationError(_("Write your message first."))
    if len(body) > limit:
        raise ValidationError(_("Keep it under %(n)s characters.") % {"n": limit})
    return body


def _live(account: OwnerAccount) -> None:
    if account.revoked_at is not None or account.is_delegate or account.organization.archived_at is not None:
        raise PermissionDenied("portal")


def _staff(actor: Membership, owner: PropertyOwner) -> None:
    services._check(actor, owner)


# Messages -----------------------------------------------------------------------------------------------------


def thread(owner: PropertyOwner) -> list[OwnerMessage]:
    latest = list(OwnerMessage.objects.filter(owner=owner).select_related("author")
                  .order_by("-created_at", "-pk")[:SHOWN])
    return latest[::-1]


@transaction.atomic
def owner_send(account: OwnerAccount, body: str) -> OwnerMessage:
    _live(account)
    body = _clean(body)
    if not ratelimit.hit(f"owner_message:{account.owner_id}", PER_HOUR, 3600):
        raise ValidationError(_("You have sent several messages just now. Wait a little, or call the office."))
    message = OwnerMessage.objects.create(organization=account.organization, owner=account.owner,
                                          side=OwnerMessage.Side.OWNER, author=account.user, body=body)
    transaction.on_commit(lambda: triggers.owner_message_received(message))
    return message


@transaction.atomic
def staff_send(actor: Membership, owner: PropertyOwner, body: str) -> OwnerMessage:
    _staff(actor, owner)
    body = _clean(body)
    message = OwnerMessage.objects.create(organization=owner.organization, owner=owner,
                                          side=OwnerMessage.Side.STAFF, author=actor.user, body=body)
    transaction.on_commit(lambda: triggers.owner_message_reply(message))
    return message


def mark_read_by_owner(owner: PropertyOwner) -> int:
    return OwnerMessage.objects.filter(owner=owner, side=OwnerMessage.Side.STAFF, read_at__isnull=True
                                       ).update(read_at=timezone.now())


def mark_read_by_staff(owner: PropertyOwner) -> int:
    return OwnerMessage.objects.filter(owner=owner, side=OwnerMessage.Side.OWNER, read_at__isnull=True
                                       ).update(read_at=timezone.now())


def unread_for_owner(account: OwnerAccount) -> int:
    return OwnerMessage.objects.filter(owner=account.owner, side=OwnerMessage.Side.STAFF,
                                       read_at__isnull=True).count()


# Approvals ----------------------------------------------------------------------------------------------------


def approvals(owner: PropertyOwner):
    return OwnerApproval.objects.filter(owner=owner).select_related("property", "expense", "maintenance_request")


def askable(owner: PropertyOwner) -> dict:
    """Repairs and expenses on the owner's properties that nobody has asked them about yet."""
    asked = OwnerApproval.objects.filter(owner=owner, status=OwnerApproval.Status.ASKED)
    repairs = (MaintenanceRequest.objects.filter(organization=owner.organization,
                                                 status__in=MaintenanceRequest.UNFINISHED)
               .filter(property__in=_props(owner)).exclude(pk__in=asked.values("maintenance_request_id"))
               .select_related("property", "unit").order_by("-created_at")[:30])
    expenses = (Expense.objects.filter(organization=owner.organization, status=Expense.Status.SUBMITTED)
                .filter(property__in=_props(owner)).exclude(pk__in=asked.values("expense_id"))
                .select_related("property").order_by("-paid_on")[:30])
    return {"repairs": list(repairs), "expenses": list(expenses)}


def _props(owner: PropertyOwner):
    from properties.models import Property

    return Property.all_objects.filter(organization=owner.organization, archived_at__isnull=True
                                       ).filter(owned_by(owner))


@transaction.atomic
def ask_approval(actor: Membership, owner: PropertyOwner, *, kind: str, subject_id, note: str = "",
                 request=None) -> OwnerApproval:
    _staff(actor, owner)
    note = (note or "").strip()[:300]
    props = _props(owner)
    fields = {"organization": owner.organization, "owner": owner, "kind": kind, "note": note,
              "asked_by": actor.user}
    if kind == OwnerApproval.Kind.REPAIR:
        subject = (MaintenanceRequest.objects.filter(organization=owner.organization, property__in=props,
                                                     public_id=subject_id).select_related("property").first())
        if subject is None or subject.status not in MaintenanceRequest.UNFINISHED:
            raise ValidationError(_("Choose a repair that is still open on this owner's properties."))
        fields.update(maintenance_request=subject, property=subject.property,
                      title=f"{subject.number} · {subject.title}"[:200], amount=subject.quote_amount)
    elif kind == OwnerApproval.Kind.EXPENSE:
        subject = (Expense.objects.filter(organization=owner.organization, property__in=props,
                                          public_id=subject_id).select_related("property").first())
        if subject is None or subject.status != Expense.Status.SUBMITTED:
            raise ValidationError(_("Choose an expense waiting for approval on this owner's properties."))
        fields.update(expense=subject, property=subject.property,
                      title=f"{subject.number} · {subject.description}"[:200], amount=subject.amount)
    else:
        raise ValidationError(_("Choose a repair or an expense."))
    try:
        with transaction.atomic():
            approval = OwnerApproval.objects.create(**fields)
    except IntegrityError:
        raise ValidationError(_("The owner has already been asked about this.")) from None
    audit.record("owner_portal.approval_ask", actor=actor.user, organization=actor.organization, obj=approval,
                 request=request, changes={"kind": kind, "title": approval.title, "amount": str(approval.amount)})
    transaction.on_commit(lambda: triggers.owner_approval_asked(approval))
    return approval


@transaction.atomic
def withdraw_approval(actor: Membership, approval: OwnerApproval, *, request=None) -> None:
    _staff(actor, approval.owner)
    approval = OwnerApproval.objects.select_for_update().get(pk=approval.pk)
    if approval.status != OwnerApproval.Status.ASKED:
        return
    approval.status = OwnerApproval.Status.WITHDRAWN
    approval.save(update_fields=["status", "updated_at"])
    audit.record("owner_portal.approval_withdraw", actor=actor.user, organization=actor.organization, obj=approval,
                 request=request)


@transaction.atomic
def answer_approval(account: OwnerAccount, approval: OwnerApproval, approve: bool, note: str = "", *,
                    request=None) -> OwnerApproval:
    _live(account)
    approval = OwnerApproval.objects.select_for_update().get(pk=approval.pk)
    if approval.owner_id != account.owner_id:
        raise PermissionDenied("portal")
    if approval.status != OwnerApproval.Status.ASKED:
        raise ValidationError(_("This has already been answered or withdrawn."))
    approval.status = OwnerApproval.Status.APPROVED if approve else OwnerApproval.Status.DECLINED
    approval.answer_note = (note or "").strip()[:300]
    approval.answered_at = timezone.now()
    approval.answered_by = account.user
    approval.save(update_fields=["status", "answer_note", "answered_at", "answered_by", "updated_at"])
    audit.record("owner_portal.approval_answer", actor=account.user, organization=account.organization,
                 obj=approval, request=request, changes={"status": approval.status})
    transaction.on_commit(lambda: triggers.owner_approval_answered(approval))
    return approval


# Payout details and requests ------------------------------------------------------------------------------------


def confirmed_detail(owner: PropertyOwner) -> OwnerPayoutDetail | None:
    return OwnerPayoutDetail.objects.filter(owner=owner, status=OwnerPayoutDetail.Status.CONFIRMED).first()


def pending_detail(owner: PropertyOwner) -> OwnerPayoutDetail | None:
    return OwnerPayoutDetail.objects.filter(owner=owner, status=OwnerPayoutDetail.Status.PENDING).first()


def open_request(owner: PropertyOwner) -> OwnerPayoutRequest | None:
    return OwnerPayoutRequest.objects.filter(owner=owner, status=OwnerPayoutRequest.Status.OPEN).first()


@transaction.atomic
def submit_detail(account: OwnerAccount, *, method: str, account_name: str, account_number: str,
                  bank_name: str = "", request=None) -> OwnerPayoutDetail:
    _live(account)
    account_name = (account_name or "").strip()[:120]
    number = re.sub(r"[\s-]", "", account_number or "")
    if method not in OwnerPayoutDetail.Method.values:
        raise ValidationError(_("Choose M-Pesa or bank transfer."))
    if not account_name:
        raise ValidationError(_("Enter the name on the account."))
    if method == OwnerPayoutDetail.Method.MPESA:
        if not PHONE.match(number):
            raise ValidationError(_("Enter the M-Pesa phone number, for example 0712345678."))
        bank_name = ""
    else:
        bank_name = (bank_name or "").strip()[:80]
        if not bank_name or not re.match(r"^[A-Za-z0-9]{5,30}$", number):
            raise ValidationError(_("Enter the bank and the account number."))
    PropertyOwner.objects.select_for_update().get(pk=account.owner_id)
    OwnerPayoutDetail.objects.filter(owner=account.owner, status=OwnerPayoutDetail.Status.PENDING).update(
        status=OwnerPayoutDetail.Status.REPLACED, updated_at=timezone.now())
    detail = OwnerPayoutDetail.objects.create(
        organization=account.organization, owner=account.owner, method=method, account_name=account_name,
        account_number=number, bank_name=bank_name, submitted_by=account.user)
    audit.record("owner_portal.payout_detail_submit", actor=account.user, organization=account.organization,
                 obj=detail, request=request, changes={"method": method, "account": detail.masked})
    transaction.on_commit(lambda: triggers.owner_payout_event(account.organization, account.owner,
                                                              _("sent new payout details to confirm")))
    return detail


@transaction.atomic
def decide_detail(actor: Membership, detail: OwnerPayoutDetail, confirm: bool, reason: str = "", *,
                  request=None) -> OwnerPayoutDetail:
    _staff(actor, detail.owner)
    PropertyOwner.objects.select_for_update().get(pk=detail.owner_id)
    detail = OwnerPayoutDetail.objects.get(pk=detail.pk)
    if detail.status != OwnerPayoutDetail.Status.PENDING:
        raise ValidationError(_("These details were already decided."))
    now = timezone.now()
    if confirm:
        OwnerPayoutDetail.objects.filter(owner=detail.owner, status=OwnerPayoutDetail.Status.CONFIRMED).update(
            status=OwnerPayoutDetail.Status.REPLACED, updated_at=now)
        detail.status = OwnerPayoutDetail.Status.CONFIRMED
    else:
        detail.status = OwnerPayoutDetail.Status.REJECTED
        detail.reject_reason = (reason or "").strip()[:200]
    detail.decided_at, detail.decided_by = now, actor.user
    detail.save(update_fields=["status", "reject_reason", "decided_at", "decided_by", "updated_at"])
    audit.record("owner_portal.payout_detail_confirm" if confirm else "owner_portal.payout_detail_reject",
                 actor=actor.user, organization=actor.organization, obj=detail, request=request,
                 changes={"account": detail.masked})
    return detail


@transaction.atomic
def request_payout(account: OwnerAccount, amount=None, note: str = "", *, request=None) -> OwnerPayoutRequest:
    _live(account)
    if confirmed_detail(account.owner) is None:
        raise ValidationError(_("Add your payout details and wait for the office to confirm them first."))
    try:
        with transaction.atomic():
            ask = OwnerPayoutRequest.objects.create(
                organization=account.organization, owner=account.owner, amount=amount or None,
                note=(note or "").strip()[:300], requested_by=account.user)
    except IntegrityError:
        raise ValidationError(_("You already have a request waiting for the office.")) from None
    except ValueError:
        raise ValidationError(_("Enter a valid amount.")) from None
    audit.record("owner_portal.payout_request", actor=account.user, organization=account.organization, obj=ask,
                 request=request, changes={"amount": str(amount or "")})
    what = _("asked to be paid") + (f" {format_money(ask.amount, account.organization.currency)}" if ask.amount else "")
    transaction.on_commit(lambda: triggers.owner_payout_event(account.organization, account.owner, what))
    return ask


@transaction.atomic
def withdraw_request(account: OwnerAccount, *, request=None) -> None:
    _live(account)
    updated = OwnerPayoutRequest.objects.filter(owner=account.owner, status=OwnerPayoutRequest.Status.OPEN
                                                ).update(status=OwnerPayoutRequest.Status.WITHDRAWN,
                                                         updated_at=timezone.now())
    if updated:
        audit.record("owner_portal.payout_request_withdraw", actor=account.user, organization=account.organization,
                     obj=account.owner, request=request)


@transaction.atomic
def handle_request(actor: Membership, ask: OwnerPayoutRequest, paid: bool, note: str = "", *,
                   request=None) -> OwnerPayoutRequest:
    _staff(actor, ask.owner)
    ask = OwnerPayoutRequest.objects.select_for_update().get(pk=ask.pk)
    if ask.status != OwnerPayoutRequest.Status.OPEN:
        raise ValidationError(_("This request was already handled."))
    ask.status = OwnerPayoutRequest.Status.DONE if paid else OwnerPayoutRequest.Status.DECLINED
    ask.answer_note = (note or "").strip()[:300]
    ask.handled_at, ask.handled_by = timezone.now(), actor.user
    ask.save(update_fields=["status", "answer_note", "handled_at", "handled_by", "updated_at"])
    audit.record("owner_portal.payout_request_done" if paid else "owner_portal.payout_request_decline",
                 actor=actor.user, organization=actor.organization, obj=ask, request=request)
    return ask


def waiting_for_office(org) -> dict:
    """Counts for the office: owner notes unread, approvals answered not yet seen are not tracked; payouts waiting."""
    return {
        "messages": OwnerMessage.objects.filter(organization=org, side=OwnerMessage.Side.OWNER,
                                                read_at__isnull=True).count(),
        "details": OwnerPayoutDetail.objects.filter(organization=org, status=OwnerPayoutDetail.Status.PENDING
                                                    ).count(),
        "requests": OwnerPayoutRequest.objects.filter(organization=org, status=OwnerPayoutRequest.Status.OPEN
                                                      ).count(),
    }
