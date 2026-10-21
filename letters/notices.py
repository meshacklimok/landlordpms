"""Notices to vacate and demand letters (D-109). Views stay thin; the rules live here.

- A notice to vacate letter is made from an open notice the landlord gave (D-106), one live letter per notice.
  Withdrawing or replacing the notice withdraws its letter.
- A demand is for arrears on the tenancy (D-048): the balance less what is unpaid on invoices still within their
  grace days, so rent billed in advance is never demanded.
- Both are frozen like tenancy letters. Only the record of service changes afterwards, audited each time.
- The wording is fixed and plain, the same for every organization, and is [VERIFY with counsel] (doc 14 D9).
"""

import datetime
import secrets
import uuid
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from accounts.models import Membership, Organization
from accounts.permissions import require, visible_properties
from audit import services as audit
from billing.models import FollowUp, Invoice, LedgerEntry
from core.money import ZERO, format_money
from core.numbering import next_number
from leases.models import Lease, LeaseNotice
from payments.forms import accounts_for
from payments.models import PaymentAccount
from properties.models import Property

from .models import NoticeLetter
from .services import latest_lease, tenancy

CAPABILITY = "leases.issue_notice"
Kind = NoticeLetter.Kind
PAY_WITHIN_DEFAULT = 7
PAY_WITHIN_MAX = 60
INVOICE_ROWS = 20
BUSINESS = (Property.Category.COMMERCIAL, Property.Category.MIXED_USE)
NUMBERING = {Kind.VACATE: ("notice_vacate", "NTV"), Kind.DEMAND: ("demand", "DEM")}


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def visible_notice_letters(membership: Membership, queryset=None):
    qs = (queryset if queryset is not None else NoticeLetter.objects.all()).filter(
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
# What applies
# ---------------------------------------------------------------------------


def landlord_notice(lease: Lease) -> LeaseNotice | None:
    """The open notice the landlord gave, which a notice to vacate letter is made from."""
    return lease.notices.filter(withdrawn_at__isnull=True, given_by=LeaseNotice.GivenBy.LANDLORD).first()


def live_vacate_letter(notice: LeaseNotice) -> NoticeLetter | None:
    return notice.letters.filter(withdrawn_at__isnull=True).first()


def vacate_problem(lease: Lease) -> str:
    if lease.status != Lease.Status.ACTIVE:
        return _("Only an active lease can be given notice.")
    notice = landlord_notice(lease)
    if notice is None:
        return _("Record the landlord's notice to vacate on the lease page first; its letter is printed from it.")
    if live_vacate_letter(notice) is not None:
        return _("This notice already has a letter. Withdraw it to issue a corrected one.")
    return ""


def arrears(chain: list[Lease], today: datetime.date) -> tuple[Decimal, list[Invoice]]:
    """The arrears on a tenancy and the invoices past their grace days with something unpaid, oldest first."""
    balance = LedgerEntry.objects.filter(lease__in=chain).aggregate(s=Sum("amount"))["s"] or ZERO
    open_invoices = list(Invoice.objects.filter(lease__in=chain, status__in=Invoice.OPEN)
                         .order_by("due_date", "pk"))
    not_yet = sum((i.total - i.amount_paid for i in open_invoices if i.overdue_after >= today), ZERO)
    late = [i for i in open_invoices if i.overdue_after < today and i.total > i.amount_paid]
    return balance - not_yet, late


def demand_problem(lease: Lease, today: datetime.date | None = None) -> str:
    if lease.is_draft:
        return _("A draft lease has nothing to demand.")
    owed, _late = arrears(tenancy(lease), today or timezone.localdate())
    if owed <= 0:
        return _("Nothing past its grace days is owed on this tenancy.")
    return ""


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


def _parties(org: Organization, lease: Lease, today: datetime.date) -> dict:
    tenants = sorted(lease.lease_tenants.select_related("tenant"), key=lambda lt: (not lt.is_primary, lt.pk))
    prop = lease.unit.property
    return {
        "organization": org.display_name,
        "org_phone": org.billing_phone,
        "org_email": org.reply_email or org.billing_email,
        "currency": lease.currency,
        "as_of": today.isoformat(),
        "tenants": [lt.tenant.name for lt in tenants],
        "property": prop.name,
        "address": prop.address_line,
        "unit": lease.unit.code,
        "lease_number": lease.number,
    }


def vacate_facts(lease: Lease, notice: LeaseNotice, *, note: str = "", print_reason: bool = True,
                 today: datetime.date | None = None, org: Organization | None = None) -> dict:
    today = today or timezone.localdate()
    org = org or Organization.objects.get(pk=lease.organization_id)
    data = _parties(org, lease, today)
    data.update({
        "given_on": notice.given_on.isoformat(), "move_out_on": notice.move_out_on.isoformat(),
        "notice_days": lease.notice_days, "shorter_agreed": notice.shorter_agreed,
        "reason": notice.reason if print_reason else "", "note": (note or "").strip()[:500],
    })
    return data


def _ways_to_pay(lease: Lease) -> list[str]:
    reference = lease.unit.payment_reference
    ways = []
    for account in accounts_for(lease).exclude(type=PaymentAccount.Type.CASH).exclude(number="")[:3]:
        if account.type == PaymentAccount.Type.PAYBILL:
            ways.append(_("M-Pesa Paybill %(number)s, account %(reference)s")
                        % {"number": account.number, "reference": reference})
        elif account.type == PaymentAccount.Type.TILL:
            ways.append(_("M-Pesa Till (Buy Goods) %(number)s") % {"number": account.number})
        elif account.type == PaymentAccount.Type.BANK:
            ways.append(_("%(bank)s, account %(number)s, reference %(reference)s")
                        % {"bank": account.display_name, "number": account.number, "reference": reference})
        else:
            ways.append(_("M-Pesa to %(number)s") % {"number": account.number})
    return ways


def demand_facts(lease: Lease, *, pay_within_days: int = PAY_WITHIN_DEFAULT, note: str = "",
                 today: datetime.date | None = None, org: Organization | None = None) -> dict:
    today = today or timezone.localdate()
    org = org or Organization.objects.get(pk=lease.organization_id)
    head = latest_lease(lease)
    owed, late = arrears(tenancy(head), today)
    listed = late[:INVOICE_ROWS]
    rest = late[INVOICE_ROWS:]
    late_total = sum((i.total - i.amount_paid for i in late), ZERO)
    data = _parties(org, head, today)
    data.update({
        "owed": str(owed),
        "invoices": [{"number": i.number, "period": i.period_label, "due": i.due_date.isoformat(),
                      "unpaid": str(i.total - i.amount_paid)} for i in listed],
        "more_count": len(rest),
        "more_unpaid": str(sum((i.total - i.amount_paid for i in rest), ZERO)),
        "other": str(owed - late_total),
        "pay_within_days": pay_within_days,
        "ways_to_pay": _ways_to_pay(head),
        "note": (note or "").strip()[:500],
    })
    return data


# ---------------------------------------------------------------------------
# Warnings before issuing
# ---------------------------------------------------------------------------


def vacate_concerns(lease: Lease, notice: LeaseNotice, today: datetime.date | None = None) -> list[str]:
    today = today or timezone.localdate()
    out = []
    if lease.unit.property.category in BUSINESS:
        out.append(_("This is a commercial or mixed-use property. A business tenancy may fall under the Landlord "
                     "and Tenant (Shops, Hotels and Catering Establishments) Act, Cap. 301, which has its own "
                     "prescribed notice form. Check with an advocate before serving this letter."))
    if notice.given_on < today:
        out.append(_("The notice is dated %(day)s, before today. A notice usually runs from the day it is served; "
                     "if it is served later, record a new notice dated that day.")
                   % {"day": _day(notice.given_on.isoformat())})
    return out


def demand_concerns(lease: Lease, today: datetime.date | None = None) -> list[str]:
    today = today or timezone.localdate()
    chain = tenancy(lease)
    out = []
    latest = FollowUp.objects.filter(lease__in=chain).order_by("-created_at", "-pk").first()
    if latest and latest.outcome == FollowUp.Outcome.PROMISED and latest.promised_on >= today:
        out.append(_("The tenant promised on %(when)s to pay by %(day)s.")
                   % {"when": _day(timezone.localdate(latest.created_at).isoformat()),
                      "day": _day(latest.promised_on.isoformat())})
    if latest and latest.outcome == FollowUp.Outcome.DISPUTED:
        out.append(_("The latest follow-up says the tenant disputes the amount."))
    for letter in NoticeLetter.objects.filter(lease__in=chain, kind=Kind.DEMAND, withdrawn_at__isnull=True,
                                              served_on__isnull=False):
        if letter.pay_by >= today:
            out.append(_("Demand %(number)s was served on %(served)s; its days run until %(day)s.")
                       % {"number": letter.number, "served": _day(letter.served_on.isoformat()),
                          "day": _day(letter.pay_by.isoformat())})
    return out


def service_concerns(letter: NoticeLetter) -> list[str]:
    """After service: a notice to vacate served too late for its move-out day [VERIFY: when a notice takes effect]."""
    if letter.kind != Kind.VACATE or not letter.is_served or letter.is_withdrawn:
        return []
    data = letter.facts
    if data["shorter_agreed"] or letter.served_on <= datetime.date.fromisoformat(data["given_on"]):
        return []
    earliest = letter.served_on + datetime.timedelta(days=data["notice_days"])
    if datetime.date.fromisoformat(data["move_out_on"]) >= earliest:
        return []
    return [_("Served on %(served)s, after the notice's date. The move-out day is less than the %(days)s days' "
              "notice from service; the tenant may be entitled to stay until %(day)s. Consider a new notice.")
            % {"served": _day(letter.served_on.isoformat()), "days": data["notice_days"],
               "day": _day(earliest.isoformat())}]


# ---------------------------------------------------------------------------
# Wording (one place, for the PDF, the preview and the check page)
# ---------------------------------------------------------------------------


def _day(iso: str) -> str:
    return date_format(datetime.date.fromisoformat(iso), "j M Y")


def premises(data: dict) -> str:
    parts = [_("Unit %(unit)s") % {"unit": data["unit"]}, data["property"], data["address"]]
    return ", ".join(p for p in parts if p)


def title(kind: str) -> str:
    return _("NOTICE TO VACATE") if kind == Kind.VACATE else _("DEMAND FOR RENT ARREARS")


def paragraphs(kind: str, data: dict) -> list[str]:
    """The body of the letter, paragraph by paragraph [VERIFY with counsel]."""
    if kind == Kind.VACATE:
        out = [
            _("We give you notice to vacate %(premises)s, let to you under lease %(lease)s, and to hand over "
              "vacant possession on or before %(day)s.")
            % {"premises": premises(data), "lease": data["lease_number"], "day": _day(data["move_out_on"])},
            _("This notice is given on %(given)s. Your lease requires %(days)s days' notice.")
            % {"given": _day(data["given_on"]), "days": data["notice_days"]},
        ]
        if data["shorter_agreed"]:
            out.append(_("You and we agreed in writing to a shorter notice."))
        if data["reason"]:
            out.append(_("Reason: %(reason)s") % {"reason": data["reason"]})
        out += [
            _("Rent and charges remain payable until you hand over the unit and its keys. We will inspect the unit "
              "with you on or before that day. Your deposit will be settled under the lease once the final "
              "account is drawn up."),
        ]
    else:
        cur = data["currency"]
        out = [
            _("Our records show that %(amount)s in rent and charges is owed and unpaid on your lease %(lease)s of "
              "%(premises)s, as at %(day)s.")
            % {"amount": format_money(Decimal(data["owed"]), cur), "lease": data["lease_number"],
               "premises": premises(data), "day": _day(data["as_of"])},
            _("We ask you to pay %(amount)s within %(days)s days of the day you receive this letter.")
            % {"amount": format_money(Decimal(data["owed"]), cur), "days": data["pay_within_days"]},
        ]
        if data["ways_to_pay"]:
            out.append(_("Pay by: %(ways)s.") % {"ways": "; ".join(data["ways_to_pay"])})
        out += [
            _("If you have paid since %(day)s, or you dispute the amount, contact us.") % {"day": _day(data["as_of"])},
            _("If the amount is not paid, we may take further steps to recover it, which may include legal action."),
        ]
    if data["note"]:
        out.append(data["note"])
    out.append(_("This letter does not take away any rights you have under the law or your lease."))
    return out


def arrears_rows(data: dict) -> list[tuple[str, str, str, str]]:
    """(invoice, period, due, unpaid) rows of a demand, with the "more" and "other" lines, ending in the total."""
    cur = data["currency"]
    rows = [(i["number"], i["period"], _day(i["due"]), format_money(Decimal(i["unpaid"]), cur))
            for i in data["invoices"]]
    if data["more_count"]:
        rows.append((ngettext("and %(n)d more invoice", "and %(n)d more invoices", data["more_count"])
                     % {"n": data["more_count"]}, "", "", format_money(Decimal(data["more_unpaid"]), cur)))
    other = Decimal(data["other"])
    if other:
        label = _("Other items on the account") if other > 0 else _("Less credit on the account")
        rows.append((label, "", "", format_money(other, cur)))
    rows.append((_("Total owed"), "", "", format_money(Decimal(data["owed"]), cur)))
    return rows


def check_rows(letter: NoticeLetter) -> list[tuple[str, list[str]]]:
    """What the public check page shows: enough to compare a copy, not the service record."""
    data = letter.facts
    rows = [
        (ngettext("Tenant", "Tenants", len(data["tenants"])), [", ".join(data["tenants"]) or "—"]),
        (_("Premises"), [premises(data)]),
        (_("Lease"), [data["lease_number"]]),
    ]
    if letter.kind == Kind.VACATE:
        rows += [(_("Notice given on"), [_day(data["given_on"])]),
                 (_("Vacate by"), [_day(data["move_out_on"])])]
    else:
        rows += [(_("Amount owed"), [_("%(amount)s as at %(day)s") % {
                     "amount": format_money(Decimal(data["owed"]), data["currency"]), "day": _day(data["as_of"])}]),
                 (_("To pay within"), [ngettext("%(n)d day of receiving it", "%(n)d days of receiving it",
                                                data["pay_within_days"]) % {"n": data["pay_within_days"]}])]
    return rows


# ---------------------------------------------------------------------------
# Issue, service and withdraw
# ---------------------------------------------------------------------------


def _new(actor: Membership, lease: Lease, kind: str, data: dict, notice: LeaseNotice | None) -> NoticeLetter:
    now = timezone.now()
    key, prefix = NUMBERING[kind]
    letter = NoticeLetter(
        organization=lease.organization, lease=lease, kind=kind, notice=notice, issued_at=now, issued_by=actor.user,
        facts=data, number=next_number(lease.organization, key, prefix=prefix,
                                       period=str(timezone.localdate(now).year)),
        verify_code=secrets.token_urlsafe(16))
    from .notice_pdf import render_pdf

    letter.pdf.save(f"{uuid.uuid4().hex}.pdf", ContentFile(render_pdf(letter)), save=False)
    letter.save()
    return letter


@transaction.atomic
def issue_vacate(actor: Membership, lease: Lease, *, note: str = "", print_reason: bool = True,
                 request=None) -> NoticeLetter:
    _check(actor, lease)
    lease = Lease.all_objects.select_for_update(of=("self",)).select_related("unit__property").get(pk=lease.pk)
    problem = vacate_problem(lease)
    if problem:
        raise ValidationError(problem)
    notice = landlord_notice(lease)
    data = vacate_facts(lease, notice, note=note, print_reason=print_reason, org=actor.organization)
    letter = _new(actor, lease, Kind.VACATE, data, notice)
    _audit("notice_letter.issue", actor, letter, request, {
        "number": [None, letter.number], "kind": [None, letter.kind], "lease": [None, lease.number],
        "move_out_on": [None, data["move_out_on"]]})
    return letter


@transaction.atomic
def issue_demand(actor: Membership, lease: Lease, *, pay_within_days: int = PAY_WITHIN_DEFAULT, note: str = "",
                 request=None) -> NoticeLetter:
    head = latest_lease(lease)
    _check(actor, head)
    if not 1 <= int(pay_within_days) <= PAY_WITHIN_MAX:
        raise ValidationError({"pay_within_days": _("Choose between 1 and %(max)s days.") % {"max": PAY_WITHIN_MAX}})
    head = Lease.all_objects.select_for_update(of=("self",)).select_related("unit__property").get(pk=head.pk)
    problem = demand_problem(head)
    if problem:
        raise ValidationError(problem)
    data = demand_facts(head, pay_within_days=int(pay_within_days), note=note, org=actor.organization)
    letter = _new(actor, head, Kind.DEMAND, data, None)
    _audit("notice_letter.issue", actor, letter, request, {
        "number": [None, letter.number], "kind": [None, letter.kind], "lease": [None, head.number],
        "owed": [None, data["owed"]], "pay_within_days": [None, data["pay_within_days"]]})
    return letter


SERVICE_FIELDS = ("served_on", "served_via", "served_by_name", "received_by", "service_note")


@transaction.atomic
def record_service(actor: Membership, letter: NoticeLetter, *, served_on: datetime.date, served_via: str,
                   served_by_name: str = "", received_by: str = "", service_note: str = "", proof_file=None,
                   request=None) -> NoticeLetter:
    """Records, or corrects, when and how a letter was served. The proof goes to the lease's documents."""
    _check(actor, letter.lease)
    letter = NoticeLetter.objects.select_for_update().get(pk=letter.pk)
    if letter.is_withdrawn:
        raise ValidationError(_("This letter was withdrawn."))
    errors = {}
    if served_on > timezone.localdate():
        errors["served_on"] = _("A letter cannot be served in the future.")
    elif served_on < timezone.localdate(letter.issued_at):
        errors["served_on"] = _("A letter cannot be served before it was issued.")
    if served_via not in NoticeLetter.ServedVia.values:
        errors["served_via"] = _("Choose how it was served.")
    if errors:
        raise ValidationError(errors)
    new = {"served_on": served_on, "served_via": served_via, "served_by_name": (served_by_name or "").strip()[:150],
           "received_by": (received_by or "").strip()[:150], "service_note": (service_note or "").strip()[:300]}
    changes = {f: [str(getattr(letter, f) or "") or None, str(v or "") or None] for f, v in new.items()
               if getattr(letter, f) != v}
    if proof_file is not None:
        from documents import services as documents
        from documents.models import Document

        if not documents.can_upload(actor, letter.lease):
            raise PermissionDenied("documents.upload")
        doc = documents.upload(actor, letter.lease, file=proof_file, category=Document.Category.NOTICE,
                               title=_("%(number)s as served") % {"number": letter.number}, request=request)
        changes["proof"] = [letter.proof.title if letter.proof_id else None, doc.title]
        letter.proof = doc
    for f, v in new.items():
        setattr(letter, f, v)
    letter.service_recorded_by, letter.service_recorded_at = actor.user, timezone.now()
    letter.save()
    if changes:
        _audit("notice_letter.serve", actor, letter, request, changes)
    return letter


@transaction.atomic
def withdraw(actor: Membership, letter: NoticeLetter, *, reason: str, request=None) -> NoticeLetter:
    """Marks a letter issued in error. The check page then says it was withdrawn; nothing is deleted."""
    _check(actor, letter.lease)
    letter = NoticeLetter.objects.select_for_update().get(pk=letter.pk)
    reason = (reason or "").strip()[:300]
    if not reason:
        raise ValidationError({"reason": _("Say why the letter is being withdrawn.")})
    if letter.is_withdrawn:
        raise ValidationError(_("This letter was already withdrawn."))
    letter.withdrawn_at, letter.withdrawn_by, letter.withdraw_reason = timezone.now(), actor.user, reason
    letter.save(update_fields=["withdrawn_at", "withdrawn_by", "withdraw_reason", "updated_at"])
    _audit("notice_letter.withdraw", actor, letter, request, {"reason": [None, reason]})
    return letter


def notice_closed(notice: LeaseNotice, *, user, reason: str) -> None:
    """The notice was withdrawn or replaced, so its letter goes too. leases.services calls it in its transaction."""
    reason = _("The notice was withdrawn: %(reason)s") % {"reason": reason} if reason else _("The notice was withdrawn")
    for letter in NoticeLetter.objects.select_for_update().filter(notice=notice, withdrawn_at__isnull=True):
        letter.withdrawn_at, letter.withdrawn_by, letter.withdraw_reason = timezone.now(), user, reason
        letter.save(update_fields=["withdrawn_at", "withdrawn_by", "withdraw_reason", "updated_at"])
        audit.record("notice_letter.withdraw", actor=user, organization=notice.organization, obj=letter,
                     changes={"reason": [None, reason]})
