"""Payments to property owners and sending them their statement (D-058).

Everything here needs the member to see every property of the owner: with only some of them,
the due figure is partial, so paying against it or sending it would mislead.
"""

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from accounts.models import Membership
from accounts.permissions import accessible_property_ids, can, require
from audit.services import record
from core.money import ZERO, format_money, round_money
from core.numbering import next_number
from notifications.delivery import notify
from properties.models import Property, PropertyOwner

from . import statements
from .metrics import add_months, month_end, month_start
from .models import OwnerRemittance, OwnerStatementSend
from .pdf import render_statement

logger = logging.getLogger(__name__)

ACCOUNT_MONTHS = 12


def visible_owner(membership: Membership, public_id) -> PropertyOwner | None:
    """The owner, if the member sees all of their properties and may see financial reports."""
    if not can(membership, "reports.view_financial"):
        return None
    owner = PropertyOwner.objects.filter(organization=membership.organization, public_id=public_id).first()
    if owner is None or not statements.fully_sees(membership, owner):
        return None
    return owner


def _check(actor: Membership, capability: str, owner: PropertyOwner) -> None:
    require(actor, capability)
    if owner.organization_id != actor.organization_id or not statements.fully_sees(actor, owner):
        raise PermissionDenied(capability)


# ---------------------------------------------------------------------------
# Remittances
# ---------------------------------------------------------------------------


@transaction.atomic
def record_remittance(actor: Membership, owner: PropertyOwner, *, month: datetime.date, amount, paid_on,
                      method: str, reference: str = "", note: str = "", request=None) -> OwnerRemittance:
    _check(actor, "owners.remit", owner)
    today = timezone.localdate()
    month = month_start(month)
    amount = round_money(Decimal(amount))
    errors = {}
    if amount <= 0:
        errors["amount"] = _("Enter an amount above zero.")
    if paid_on > today:
        errors["paid_on"] = _("The date paid cannot be in the future.")
    if month > month_start(today):
        errors["month"] = _("The month cannot be in the future.")
    if method not in OwnerRemittance.Method.values:
        errors["method"] = _("Choose a method.")
    if errors:
        raise ValidationError(errors)
    remittance = OwnerRemittance.objects.create(
        organization=actor.organization, owner=owner, month=month, amount=amount, paid_on=paid_on, method=method,
        reference=reference.strip(), note=note.strip(), recorded_by=actor.user)
    record("owner_remittance.record", actor=actor.user, organization=actor.organization, obj=remittance,
           changes={"owner": [None, owner.name], "month": [None, f"{month:%Y-%m}"], "amount": [None, str(amount)],
                    "paid_on": [None, paid_on.isoformat()], "method": [None, method]}, request=request)
    return remittance


@transaction.atomic
def void_remittance(actor: Membership, remittance: OwnerRemittance, reason: str, request=None) -> OwnerRemittance:
    remittance = OwnerRemittance.objects.select_for_update().get(pk=remittance.pk)
    _check(actor, "owners.remit", remittance.owner)
    if remittance.is_void:
        raise ValidationError(_("This payment is already voided."))
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": _("Say why the payment is voided.")})
    remittance.voided_at = timezone.now()
    remittance.voided_by = actor.user
    remittance.void_reason = reason[:300]
    remittance.save(update_fields=["voided_at", "voided_by", "void_reason", "updated_at"])
    record("owner_remittance.void", actor=actor.user, organization=actor.organization, obj=remittance,
           changes={"voided": [False, True], "reason": [None, remittance.void_reason]}, request=request)
    return remittance


# ---------------------------------------------------------------------------
# The owner's account
# ---------------------------------------------------------------------------


@dataclass
class AccountRow:
    month: datetime.date
    collected: Decimal = ZERO
    fee: Decimal = ZERO
    due: Decimal = ZERO
    remitted: Decimal = ZERO

    @property
    def remaining(self) -> Decimal:
        return self.due - self.remitted


@dataclass
class Account:
    owner: PropertyOwner
    rows: list[AccountRow]
    remittances: list[OwnerRemittance]

    @property
    def remaining(self) -> Decimal:
        return sum((r.remaining for r in self.rows), ZERO)

    @property
    def due(self) -> Decimal:
        return sum((r.due for r in self.rows), ZERO)

    @property
    def remitted(self) -> Decimal:
        return sum((r.remitted for r in self.rows), ZERO)


def account(membership: Membership, owner: PropertyOwner, today: datetime.date | None = None) -> Account:
    """The last 12 finished months, newest first, and every payment to the owner."""
    last = statements.default_month(today)
    rows = []
    for i in range(ACCOUNT_MONTHS):
        month = add_months(last, -i)
        st = statements.statement(membership, str(owner.public_id), month)
        row = AccountRow(month=month)
        if st is not None:
            row.collected, row.fee, row.due, row.remitted = st.collected, st.fee, st.due, st.remitted
        rows.append(row)
    remittances = list(OwnerRemittance.objects.filter(owner=owner).select_related("recorded_by", "voided_by"))
    return Account(owner=owner, rows=rows, remittances=remittances)


# ---------------------------------------------------------------------------
# Sending the statement
# ---------------------------------------------------------------------------


def send_blocker(membership: Membership, st: statements.Statement, today: datetime.date | None = None) -> str | None:
    """Why this statement cannot be sent, or None. The button shows only when this is None."""
    today = today or timezone.localdate()
    if st.owner is None or not can(membership, "owners.send_statement"):
        return _("Not allowed.")
    if not st.shows_remittances:
        return _("You do not see all of this owner's properties.")
    if month_end(st.month) >= today:
        return _("The month is not over yet.")
    if not (st.owner.email or st.owner.phone):
        return _("Add the owner's email or phone first.")
    return None


def statement_filename(st: statements.Statement) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in st.recipient.lower()).strip("-")[:40] or "owner"
    return f"owner-statement-{slug}-{st.month:%Y-%m}.pdf"


def send_statement(actor: Membership, st: statements.Statement, *, sms: bool = False,
                   request=None) -> OwnerStatementSend:
    """Emails the PDF (when the owner has an email) and texts a summary (when asked and they have a phone)."""
    blocker = send_blocker(actor, st)
    if blocker:
        raise ValidationError(blocker)
    owner = st.owner
    _check(actor, "owners.send_statement", owner)
    sms = sms and bool(owner.phone)
    if not (owner.email or sms):
        raise ValidationError(_("The owner has no email. Tick the SMS box to text them the summary."))
    org = actor.organization
    cur = st.currency
    with transaction.atomic():
        pdf = render_statement(st)
        now = timezone.now()
        send = OwnerStatementSend(
            organization=org, owner=owner, month=st.month, collected=st.collected, fee=st.fee, expenses=st.expenses,
            due=st.due, remitted=st.remitted, email_to=owner.email, sent_by=actor.user, sent_at=now,
            number=next_number(org, "owner_statement", prefix="OST", period=str(timezone.localdate().year)))
        send.pdf.save(statement_filename(st), ContentFile(pdf), save=False)
        if sms:
            send.sms = notify(org, "owner_statement", phone=owner.phone, created_by=actor.user, context={
                "owner_name": owner.name, "month": st.label, "collected": format_money(st.collected, cur),
                "fee": format_money(st.fee, cur), "expenses": format_money(st.expenses, cur),
                "due": format_money(st.due, cur),
                "remitted": format_money(st.remitted, cur), "remaining": format_money(st.remaining, cur)})
        send.save()
        record("owner_statement.send", actor=actor.user, organization=org, obj=send, request=request,
               changes={"owner": [None, owner.name], "month": [None, f"{st.month:%Y-%m}"],
                        "expenses": [None, str(st.expenses)], "due": [None, str(st.due)],
                        "remitted": [None, str(st.remitted)],
                        "email": [None, owner.email], "sms": [None, owner.phone if sms else ""]})
    if owner.email:
        _email(send, st, pdf)
    return send


def _email(send: OwnerStatementSend, st: statements.Statement, pdf: bytes) -> None:
    org = st.organization
    cur = st.currency
    lines = [
        _("Dear %(owner)s,") % {"owner": st.owner.name},
        "",
        _("Please find attached your statement for %(month)s (%(number)s).") % {"month": st.label,
                                                                                "number": send.number},
        "",
        _("Collected: %(v)s") % {"v": format_money(st.collected, cur)},
        _("Management fee: %(v)s") % {"v": format_money(st.fee, cur)},
        _("Expenses: %(v)s") % {"v": format_money(st.expenses, cur)},
        _("Due to you: %(v)s") % {"v": format_money(st.due, cur)},
        _("Paid to you: %(v)s") % {"v": format_money(st.remitted, cur)},
        _("Still to pay: %(v)s") % {"v": format_money(st.remaining, cur)},
        "",
        org.display_name,
    ]
    message = EmailMessage(_("Owner statement for %(month)s") % {"month": st.label}, "\n".join(lines), None,
                           [send.email_to])
    message.attach(statement_filename(st), pdf, "application/pdf")
    try:
        message.send()
    except Exception as exc:  # the provider's error, whatever it is, is shown on the page
        logger.warning("Owner statement %s: email failed: %s", send.number, exc)
        send.email_status, send.email_error = OwnerStatementSend.EmailStatus.FAILED, str(exc)[:300]
    else:
        send.email_status = OwnerStatementSend.EmailStatus.SENT
    send.save(update_fields=["email_status", "email_error"])


def visible_sends(membership: Membership, owner: PropertyOwner, month: datetime.date):
    return (OwnerStatementSend.objects.filter(organization=membership.organization, owner=owner, month=month)
            .select_related("sent_by", "sms"))


# ---------------------------------------------------------------------------
# Home page
# ---------------------------------------------------------------------------


def to_send(membership: Membership, today: datetime.date | None = None) -> tuple[int, datetime.date]:
    """Owners with a contact, fully seen by the member, with no statement sent for last month."""
    month = statements.default_month(today)
    if not (can(membership, "owners.send_statement") and can(membership, "reports.view_financial")):
        return 0, month
    org = membership.organization
    owners = (PropertyOwner.objects.filter(organization=org, properties__in=Property.objects.for_org(org))
              .exclude(email="", phone="").exclude(statement_sends__month=month).distinct())
    ids = accessible_property_ids(membership)
    if ids is not None:
        owners = owners.exclude(pk__in=Property.all_objects.filter(owner__isnull=False).exclude(pk__in=ids)
                                .values("owner_id"))
    n = owners.count()
    return n, month


def to_send_label(n: int) -> str:
    return ngettext("owner statement to send", "owner statements to send", n)
