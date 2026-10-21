"""Assistant actions (D-092): the model proposes, the person confirms.

The `propose_*` tools write nothing. Each checks the member's permission, finds what the proposal
refers to among what the member can see, and hands back a proposal: a plain dict kept in the
session under a random id, with the ids it refers to and the lines shown on the confirm card.
`confirm` runs the page's own service as the member, so permissions are checked again then.
"""

import datetime
import uuid

from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership, User
from accounts.permissions import can
from audit import services as audit
from core.money import parse_money
from expenses.models import Supplier
from leases.models import Lease
from leases.services import visible_leases
from maintenance import services as maintenance_services
from maintenance.models import MaintenanceRequest
from notifications import announcements
from payments import services as payment_services
from payments.models import Payment

from . import tools

MAX_PROPOSALS = 3
EXPIRES = datetime.timedelta(minutes=30)
PROPOSED = "shown to the person as a card to confirm; nothing is done until they press Confirm"

PAYMENT, MESSAGE, ASSIGNMENT = "payment", "message", "assignment"


def _proposal(kind: str, data: dict, lines: list, **extra) -> dict:
    return {"id": str(uuid.uuid4()), "kind": kind, "created": timezone.now().isoformat(), "state": "open",
            "data": data, "lines": [[str(label), str(value)] for label, value in lines if value not in (None, "")],
            **extra}


def _errors(exc: ValidationError) -> dict:
    return {"error": "cannot propose this: " + " ".join(exc.messages)}


def _lease_lines(m: Membership, lease: Lease) -> list:
    names = tools.tenant_names(m, lease)
    return [(_("Lease"), lease.number), (_("Unit"), f"{lease.unit.code} · {lease.unit.property.name}"),
            (_("Tenants"), ", ".join(names) if names else "")]


# ---------------------------------------------------------------------------
# The proposal tools (item 1)
# ---------------------------------------------------------------------------


def propose_payment(m: Membership, args: dict, today: datetime.date) -> dict:
    if not can(m, "payments.record"):
        return tools.NOT_ALLOWED
    lease, error = tools.find_lease(m, args.get("lease_number_or_unit"), args.get("property"))
    if error:
        return error
    if not can(m, "payments.record", lease.unit.property):
        return tools.NOT_ALLOWED
    if lease.archived_at is not None:
        return {"error": "payments can only be recorded on an activated lease"}
    try:
        amount = parse_money(str(args.get("amount") or ""), allow_zero=False)
    except ValidationError:
        return {"error": "give the amount as a number above zero"}
    method = str(args.get("method") or "").upper().replace("-", "")
    if method not in Payment.Method.values:
        return {"error": "say how it was paid: " + ", ".join(Payment.Method.values)}
    try:
        paid_at = datetime.date.fromisoformat(tools._text(args.get("date"), 10)) if args.get("date") else today
    except ValueError:
        return {"error": "give the date as YYYY-MM-DD"}
    if paid_at > today:
        return {"error": "the date cannot be in the future"}
    reference = tools._text(args.get("reference"), 60)
    review = not can(m, "payments.confirm", lease.unit.property)
    lines = [*_lease_lines(m, lease), (_("Amount"), tools._money(m, amount)),
             (_("Paid by"), Payment.Method(method).label), (_("Date"), paid_at.strftime("%d %b %Y")),
             (_("Reference"), reference),
             (_("Review"), _("It will wait for someone who can confirm payments.") if review else "")]
    proposal = _proposal(PAYMENT, {"lease": lease.pk, "amount": str(amount), "method": method,
                                   "paid_at": paid_at.isoformat(), "reference": reference},
                         lines, title=_("Record a payment"))
    return {"status": PROPOSED, "lease": lease.number, "amount": tools._money(m, amount), "method": method,
            "date": paid_at.isoformat(), "needs_review_after": review, "proposal": proposal}


def propose_message(m: Membership, args: dict, today: datetime.date) -> dict:
    if not can(m, "messages.send_bulk"):
        return tools.NOT_ALLOWED
    lease, error = tools.find_lease(m, args.get("lease_number_or_unit"), args.get("property"))
    if error:
        return error
    if not can(m, "messages.send_bulk", lease.unit.property):
        return tools.NOT_ALLOWED
    try:
        preview = announcements.preview(m, str(args.get("text") or ""), _audience(lease), today)
    except ValidationError as exc:
        return _errors(exc)
    if not preview.recipients:
        return {"error": "no current tenant on that lease can receive a message"}
    text = announcements.validate_text(m.organization, str(args.get("text") or ""))
    lines = [*_message_lines(preview), (_("Lease"), lease.number),
             (_("Unit"), f"{lease.unit.code} · {lease.unit.property.name}")]
    proposal = _proposal(MESSAGE, {"lease": lease.pk}, lines, title=_("Send a message"), text=text)
    return {"status": PROPOSED, "lease": lease.number, "recipients": len(preview.recipients),
            "receiving": preview.sending, "proposal": proposal}


def _audience(lease: Lease) -> announcements.Audience:
    return announcements.Audience(properties=(lease.unit.property,), leases=(lease,))


def _message_lines(preview) -> list:
    to = [r.tenant.name + (f" ({r.skip_label})" if r.skip_reason else "") for r in preview.recipients]
    return [(_("To"), ", ".join(to)), (_("SMS cost"), str(preview.cost) if preview.parts else "")]


def _one(items: list, name: str, what: str) -> tuple[object | None, dict | None]:
    found = [x for x in items if str(x).lower() == name.lower()] or [x for x in items if name.lower() in str(x).lower()]
    if len(found) == 1:
        return found[0], None
    if not found:
        return None, {"error": f"no {what} matches {name!r}"}
    return None, {"error": f"more than one {what} matches; ask which", "matches": [str(x) for x in found[:6]]}


def propose_assignment(m: Membership, args: dict, today: datetime.date) -> dict:
    if not can(m, "maintenance.assign"):
        return tools.NOT_ALLOWED
    number = tools._text(args.get("repair_number"), 30)
    req = maintenance_services.visible_requests(m).filter(number__iexact=number).select_related(
        "property", "assigned_to", "supplier").first() if number else None
    if req is None:
        return {"error": f"no repair this member can see has the number {number!r}"}
    if not can(m, "maintenance.assign", req.property):
        return tools.NOT_ALLOWED
    if req.status not in MaintenanceRequest.OPEN:
        return {"error": "only an open repair can be assigned"}
    staff_name, supplier_name = tools._text(args.get("staff")), tools._text(args.get("supplier"))
    if not staff_name and not supplier_name:
        return {"error": "say which staff member or supplier"}
    user, supplier = req.assigned_to, req.supplier
    if staff_name:
        user, error = _one(maintenance_services.assignable_users(req), staff_name,
                           "staff member who can see this property's repairs")
        if error:
            return error
    if supplier_name:
        supplier, error = _one(list(Supplier.objects.filter(organization=m.organization)), supplier_name,
                               "supplier")
        if error:
            return error
    if user == req.assigned_to and supplier == req.supplier:
        return {"error": "the repair is already assigned that way"}
    lines = [(_("Repair"), f"{req.number} · {req.title}"), (_("Where"), f"{req.property.name} · {req.where}"),
             (_("Staff"), str(user) if user else _("nobody")), (_("Supplier"), str(supplier) if supplier else ""),
             (_("SMS"), _("The supplier gets an SMS with the job.") if supplier and supplier != req.supplier
              and supplier.phone else "")]
    proposal = _proposal(ASSIGNMENT, {"request": req.pk, "user": user.pk if user else None,
                                      "supplier": supplier.pk if supplier else None},
                         lines, title=_("Assign a repair"))
    return {"status": PROPOSED, "repair": req.number, "staff": str(user) if user else None,
            "supplier": str(supplier) if supplier else None, "proposal": proposal}


_LEASE = {"type": "string", "description": "A lease number or a unit code."}

TOOLS: tuple[tools.Tool, ...] = (
    tools.Tool("propose_payment", "Propose recording money a tenant paid. Shows the person a card to confirm; "
               "records nothing itself.", {"type": "object", "properties": {
                   "lease_number_or_unit": _LEASE, "property": tools._PROPERTY,
                   "amount": {"type": "number", "description": "The amount paid."},
                   "method": {"type": "string", "enum": list(Payment.Method.values)},
                   "date": {"type": "string", "description": "Optional: YYYY-MM-DD. Default: today."},
                   "reference": {"type": "string", "description": "Optional: M-Pesa code, slip or cheque number."}},
                   "required": ["lease_number_or_unit", "amount", "method"]}, propose_payment),
    tools.Tool("propose_message", "Propose a text message to the tenants on one lease. Shows the person a card to "
               "confirm; sends nothing itself. May use {tenant_name}, {unit} and {property}.",
               {"type": "object", "properties": {
                   "lease_number_or_unit": _LEASE, "property": tools._PROPERTY,
                   "text": {"type": "string", "description": "The message, short, as an SMS."}},
                "required": ["lease_number_or_unit", "text"]}, propose_message),
    tools.Tool("propose_assignment", "Propose assigning an open repair to a staff member, a supplier or both. "
               "Shows the person a card to confirm; changes nothing itself.", {"type": "object", "properties": {
                   "repair_number": {"type": "string"},
                   "staff": {"type": "string", "description": "Optional: the staff member's name."},
                   "supplier": {"type": "string", "description": "Optional: the supplier's name."}},
                   "required": ["repair_number"]}, propose_assignment),
)
NAMES = {t.name for t in TOOLS}


# ---------------------------------------------------------------------------
# Confirming (items 3-4)
# ---------------------------------------------------------------------------


def is_expired(proposal: dict, now: datetime.datetime | None = None) -> bool:
    try:
        created = datetime.datetime.fromisoformat(proposal["created"])
    except (KeyError, TypeError, ValueError):
        return True
    return (now or timezone.now()) - created > EXPIRES


def _lease(m: Membership, pk) -> Lease:
    lease = visible_leases(m).select_related("unit__property").filter(pk=pk).first()
    if lease is None:
        raise PermissionDenied("lease")
    return lease


def confirm(m: Membership, proposal: dict, *, text: str | None = None, request=None) -> tuple[str, str]:
    """Does what the proposal says, as the member. Returns (what happened, a link to it).

    The proposal lives in the session, so two quick submits both see it open. The cache claim
    lets only one of them run; a failed run gives the claim back so the person can try again.
    """
    if proposal.get("state") != "open":
        raise ValidationError(_("That proposal was already used."))
    if is_expired(proposal):
        raise ValidationError(_("That proposal has expired. Ask again."))
    claim = f"assistant-proposal:{proposal.get('id')}"
    if not cache.add(claim, 1, timeout=int(EXPIRES.total_seconds()) * 2):
        raise ValidationError(_("That proposal was already used."))
    try:
        return _run(m, proposal, text=text, request=request)
    except BaseException:
        cache.delete(claim)
        raise


@transaction.atomic
def _run(m: Membership, proposal: dict, *, text: str | None, request) -> tuple[str, str]:
    kind, data = proposal.get("kind"), proposal.get("data") or {}
    if kind == PAYMENT:
        payment = payment_services.record_payment(
            m, _lease(m, data["lease"]), amount=data["amount"], method=data["method"],
            paid_at=datetime.date.fromisoformat(data["paid_at"]), reference=data["reference"], request=request)
        obj, url = payment, reverse("payments:detail", args=[payment.public_id])
        done = (_("Payment recorded.") if payment.status == Payment.Status.CONFIRMED
                else _("Payment recorded. It waits for review."))
    elif kind == MESSAGE:
        announcement, _created = announcements.send(
            m, text if text is not None else proposal.get("text", ""), _audience(_lease(m, data["lease"])),
            nonce=uuid.UUID(proposal["id"]), request=request)
        obj, url = announcement, reverse("notifications:announcement", args=[announcement.public_id])
        done = _("Message sent.")
    elif kind == ASSIGNMENT:
        req = maintenance_services.visible_requests(m).filter(pk=data["request"]).first()
        if req is None:
            raise PermissionDenied("request")
        user = User.objects.filter(pk=data["user"]).first() if data.get("user") else None
        supplier = (Supplier.objects.filter(organization=m.organization, pk=data["supplier"]).first()
                    if data.get("supplier") else None)
        if (data.get("user") and user is None) or (data.get("supplier") and supplier is None):
            raise ValidationError(_("That person or supplier is no longer available. Ask again."))
        req = maintenance_services.assign(m, req, user=user, supplier=supplier, request=request)
        obj, url = req, reverse("maintenance:detail", args=[req.public_id])
        done = _("Repair assigned.")
    else:
        raise ValidationError(_("Unknown proposal."))
    audit.record("assistant.confirmed", actor=m.user, organization=m.organization, obj=obj, request=request,
                 changes={"action": [None, kind]})
    return str(done), url
