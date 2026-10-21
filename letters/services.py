"""Tenancy and payment record letters (D-048). Views stay thin; the rules live here.

- A tenancy is a lease and the leases it renewed or moved from (previous_lease), oldest first.
  A letter asked for on any of them is about the whole tenancy and is filed on the latest lease.
- The letter states facts only, never a judgment, so it can be issued whatever the record shows.
  The organization chooses which of payment record, balance, deposit and rent it states.
- On time means the invoice was paid in full by payments dated on or before its overdue_after
  (the due date plus the lease's grace days). Only invoices whose grace has run out are counted.
"""

import datetime
import secrets
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from accounts.models import Membership, Organization
from accounts.permissions import require, visible_properties
from audit import services as audit
from billing.models import DepositEntry, Invoice, LedgerEntry
from core.money import ZERO, format_money
from core.numbering import next_number
from leases.models import Lease
from payments.models import Payment
from properties.models import Property

from .models import TenancyLetter

CAPABILITY = "tenants.issue_letter"
SETTINGS = ("letter_show_payment_record", "letter_show_balance", "letter_show_deposit", "letter_show_rent")
DepositKind = DepositEntry.Kind


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def visible_letters(membership: Membership, queryset=None):
    qs = (queryset if queryset is not None else TenancyLetter.objects.all()).filter(
        organization=membership.organization)
    return qs.filter(lease__unit__property__in=visible_properties(membership, Property.all_objects.all()))


def _check(actor: Membership, lease: Lease) -> None:
    if lease.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, CAPABILITY, lease.unit.property)


def _audit(action, actor, obj, request, changes=None):
    audit.record(action, actor=actor.user, organization=actor.organization, obj=obj, request=request,
                 changes=changes or {})


# ---------------------------------------------------------------------------
# The tenancy
# ---------------------------------------------------------------------------


def latest_lease(lease: Lease) -> Lease:
    """The issued lease that renewed or replaced this one, and so on, to the newest."""
    seen = {lease.pk}
    while True:
        nxt = (Lease.all_objects.filter(previous_lease=lease).exclude(status=Lease.Status.DRAFT)
               .select_related("unit__property").first())
        if nxt is None or nxt.pk in seen:
            return lease
        seen.add(nxt.pk)
        lease = nxt


def tenancy(lease: Lease) -> list[Lease]:
    """The leases of this tenancy, oldest first, ending with the latest."""
    chain = [latest_lease(lease)]
    while chain[-1].previous_lease_id and len(chain) < 100:
        chain.append(Lease.all_objects.select_related("unit__property").get(pk=chain[-1].previous_lease_id))
    return chain[::-1]


def issue_problem(lease: Lease) -> str:
    """Why no letter can be issued for this lease's tenancy, or ""."""
    if lease.is_draft:
        return _("A draft lease has no tenancy to report yet.")
    return ""


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


def _payment_record(chain: list[Lease], today: datetime.date) -> dict:
    money = DecimalField(max_digits=14, decimal_places=2)
    on_time = Sum("payment_allocations__amount", output_field=money,
                  filter=Q(payment_allocations__payment__status=Payment.Status.CONFIRMED,
                           payment_allocations__payment__paid_at__lte=F("overdue_after")))
    invoices = (Invoice.objects.filter(lease__in=chain, status__in=(*Invoice.OPEN, Invoice.Status.PAID),
                                       total__gt=0, overdue_after__lt=today)
                .annotate(paid_on_time=Coalesce(on_time, Value(ZERO), output_field=money))
                .order_by("due_date"))
    counts = {"due": 0, "on_time": 0, "late": 0, "unpaid": 0}
    first = last = None
    for inv in invoices:
        counts["due"] += 1
        first, last = first or inv.due_date, inv.due_date
        if inv.paid_on_time >= inv.total:
            counts["on_time"] += 1
        elif inv.amount_paid >= inv.total:
            counts["late"] += 1
        else:
            counts["unpaid"] += 1
    return {**counts, "first": first.isoformat() if first else None, "last": last.isoformat() if last else None}


def _deposit(chain: list[Lease]) -> dict:
    """What came in and went out over the tenancy. Transfers between its leases cancel out;
    a corrected entry counts net of its correction."""
    entries = list(DepositEntry.objects.filter(lease__in=chain).select_related("reversed_by"))
    totals = {DepositKind.RECEIVED: ZERO, DepositKind.DEDUCTION: ZERO, DepositKind.REFUNDED: ZERO}
    for e in entries:
        if e.kind in totals:
            reversal = getattr(e, "reversed_by", None)
            totals[e.kind] += e.amount + (reversal.amount if reversal else ZERO)
    return {"received": str(totals[DepositKind.RECEIVED]), "deducted": str(-totals[DepositKind.DEDUCTION]),
            "refunded": str(-totals[DepositKind.REFUNDED]), "held": str(sum((e.amount for e in entries), ZERO))}


def facts(lease: Lease, *, today: datetime.date | None = None, org: Organization | None = None) -> dict:
    """Everything the letter states, as plain JSON. Amounts are strings; dates are ISO."""
    today = today or timezone.localdate()
    org = org or Organization.objects.get(pk=lease.organization_id)
    chain = tenancy(lease)
    head = chain[-1]
    current = head.status == Lease.Status.ACTIVE
    tenants = sorted(head.lease_tenants.select_related("tenant"), key=lambda lt: (not lt.is_primary, lt.pk))
    data = {
        "organization": org.display_name,
        "currency": head.currency,
        "as_of": today.isoformat(),
        "tenants": [lt.tenant.name for lt in tenants],
        "current": current,
        "periods": [{"property": ls.unit.property.name, "unit": ls.unit.code, "start": ls.start_date.isoformat(),
                     "end": None if ls is head and current else (ls.effective_end or today).isoformat()}
                    for ls in chain],
    }
    if org.letter_show_rent:
        day = today if current else (head.effective_end or today)
        rent = head.rent_on(max(day, head.start_date))
        data["rent"] = str(rent) if rent is not None else None
    if org.letter_show_payment_record:
        data["payment_record"] = _payment_record(chain, today)
    if org.letter_show_balance:
        balance = LedgerEntry.objects.filter(lease__in=chain).aggregate(s=Sum("amount"))["s"] or ZERO
        data["balance"] = str(balance)
    if org.letter_show_deposit:
        data["deposit"] = _deposit(chain)
    return data


# ---------------------------------------------------------------------------
# Wording (one place, for the PDF and the check page)
# ---------------------------------------------------------------------------


def _day(iso: str) -> str:
    return date_format(datetime.date.fromisoformat(iso), "j M Y")


def _month(iso: str) -> str:
    return date_format(datetime.date.fromisoformat(iso), "M Y")


def statements(data: dict) -> list[tuple[str, list[str]]]:
    """(label, lines) rows in the order the letter prints them."""
    cur = data["currency"]

    def money(s):
        return format_money(Decimal(s), cur)

    rows = [(ngettext("Tenant", "Tenants", len(data["tenants"])), [", ".join(data["tenants"]) or "—"])]
    periods = []
    for p in data["periods"]:
        span = (_("%(start)s – %(end)s") % {"start": _day(p["start"]), "end": _day(p["end"])} if p["end"]
                else _("%(start)s to date") % {"start": _day(p["start"])})
        periods.append(f"{p['property']} · {_('Unit')} {p['unit']}: {span}")
    rows.append((_("Tenancy"), periods))
    last = data["periods"][-1]
    rows.append((_("Status"), [_("Current tenant") if data["current"]
                               else _("Tenancy ended on %(day)s") % {"day": _day(last["end"])}]))

    if "rent" in data:
        label = _("Monthly rent") if data["current"] else _("Monthly rent at the end")
        rows.append((label, [money(data["rent"]) if data["rent"] else "—"]))

    if "payment_record" in data:
        r = data["payment_record"]
        if not r["due"]:
            lines = [_("No rent had fallen due yet.")]
        else:
            span = (_month(r["first"]) if r["first"][:7] == r["last"][:7]
                    else _("%(first)s to %(last)s") % {"first": _month(r["first"]), "last": _month(r["last"])})
            lines = [
                ngettext("%(n)d invoice fell due (%(span)s).", "%(n)d invoices fell due (%(span)s).", r["due"])
                % {"n": r["due"], "span": span},
                _("Paid on time: %(on_time)d · Paid late: %(late)d · Not yet paid in full: %(unpaid)d")
                % {"on_time": r["on_time"], "late": r["late"], "unpaid": r["unpaid"]},
                _("On time means paid in full by the due date or within the grace days in the lease."),
            ]
        rows.append((_("Payment record"), lines))

    if "balance" in data:
        balance, as_of = Decimal(data["balance"]), _day(data["as_of"])
        if balance > 0:
            line = _("%(amount)s owed as at %(day)s.") % {"amount": money(balance), "day": as_of}
        elif balance < 0:
            line = _("%(amount)s in credit as at %(day)s.") % {"amount": money(-balance), "day": as_of}
        else:
            line = _("Nothing owed as at %(day)s.") % {"day": as_of}
        rows.append((_("Balance"), [line]))

    if "deposit" in data:
        d = {k: Decimal(v) for k, v in data["deposit"].items()}
        if not d["received"] and not d["held"]:
            lines = [_("No deposit recorded.")]
        elif data["current"] or d["held"] > 0:
            lines = [_("%(amount)s held.") % {"amount": money(d["held"])}]
            if not data["current"]:
                lines.append(_("Not yet cleared."))
        else:
            parts = []
            if d["refunded"]:
                parts.append(_("%(amount)s refunded") % {"amount": money(d["refunded"])})
            if d["deducted"]:
                parts.append(_("%(amount)s deducted") % {"amount": money(d["deducted"])})
            lines = [_("Cleared: %(parts)s.") % {"parts": ", ".join(parts)} if parts else _("Cleared.")]
        rows.append((_("Deposit"), lines))
    return rows


def concerns(data: dict) -> list[str]:
    """What the issuer should know before the letter goes out: it states these as they are."""
    out = []
    r = data.get("payment_record")
    if r and (r["late"] or r["unpaid"]):
        out.append(_("The payment record shows late or unpaid invoices."))
    if "balance" in data and Decimal(data["balance"]) > 0:
        out.append(_("The tenant owes money today."))
    return out


# ---------------------------------------------------------------------------
# Issue, withdraw and settings
# ---------------------------------------------------------------------------


def verify_url(letter: TenancyLetter) -> str:
    return settings.SITE_URL + reverse("letter_check", args=[letter.verify_code])


@transaction.atomic
def issue(actor: Membership, lease: Lease, *, request=None) -> TenancyLetter:
    head = latest_lease(lease)
    _check(actor, head)
    problem = issue_problem(head)
    if problem:
        raise ValidationError(problem)
    now = timezone.now()
    letter = TenancyLetter(
        organization=head.organization, lease=head, issued_at=now, issued_by=actor.user,
        facts=facts(head, today=timezone.localdate(now), org=actor.organization),
        number=next_number(head.organization, "letter", prefix="LTR", period=str(timezone.localdate(now).year)),
        verify_code=secrets.token_urlsafe(16))
    from .pdf import render_pdf

    letter.pdf.save(f"{uuid.uuid4().hex}.pdf", ContentFile(render_pdf(letter)), save=False)
    letter.save()
    _audit("letter.issue", actor, letter, request, {"number": [None, letter.number], "lease": [None, head.number],
                                                    "states": [None, sorted(k for k in letter.facts if k in (
                                                        "rent", "payment_record", "balance", "deposit"))]})
    return letter


@transaction.atomic
def withdraw(actor: Membership, letter: TenancyLetter, *, reason: str, request=None) -> TenancyLetter:
    """Marks a letter issued in error. The check page then says it was withdrawn; nothing is deleted."""
    _check(actor, letter.lease)
    letter = TenancyLetter.objects.select_for_update().get(pk=letter.pk)
    reason = (reason or "").strip()[:300]
    if not reason:
        raise ValidationError({"reason": _("Say why the letter is being withdrawn.")})
    if letter.is_withdrawn:
        raise ValidationError(_("This letter was already withdrawn."))
    letter.withdrawn_at, letter.withdrawn_by, letter.withdraw_reason = timezone.now(), actor.user, reason
    letter.save(update_fields=["withdrawn_at", "withdrawn_by", "withdraw_reason", "updated_at"])
    _audit("letter.withdraw", actor, letter, request, {"reason": [None, reason]})
    return letter


def save_settings(actor: Membership, *, request=None, **values) -> None:
    require(actor, "organization.manage")
    org = actor.organization
    changes = {}
    for field in SETTINGS:
        new = bool(values.get(field))
        if getattr(org, field) != new:
            changes[field] = [getattr(org, field), new]
            setattr(org, field, new)
    if changes:
        org.save(update_fields=[*changes, "updated_at"])
        audit.record("letters.settings", actor=actor.user, organization=org, obj=org, request=request,
                     changes=changes)
