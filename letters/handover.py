"""The advocate handover pack (D-122): one ZIP with what an advocate asks for before acting on a tenancy.

For a tenant in arrears or a disputed move-out. Nothing is stored: the pack is drawn from the records each time,
and the audit event keeps its SHA-256 and what it claimed, so the copy handed over can be identified later.

- A brief: the parties, the premises, the lease, the amount, what is unpaid and how late, the letters served,
  the follow-ups, and what to check before acting.
- The statement of account over the whole tenancy (PDF and CSV), payments and follow-ups (CSV).
- The notice and demand letters as issued, with the signed copies recorded when they were served.
- For a move-out: the move-out statement and the condition reports with their photos.
- The lease's documents, and a certificate under the Evidence Act s.106B for a responsible person to sign.

What each member sees follows their permissions: ID numbers, payments, photos and documents are left out, and the
brief says so, when they could not open them in the app. The wording is [VERIFY with counsel].
"""

import csv
import datetime
import hashlib
import io
import json
import re
import zipfile
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from accounts.models import Membership
from accounts.permissions import can
from audit import services as audit
from audit.models import AuditEvent
from billing.models import DepositEntry, FollowUp, LedgerEntry
from billing.selectors import AGING_BUCKETS, lease_arrears
from core.money import ZERO, format_money
from documents.models import Document
from leases import paperwork
from leases.models import Lease, LeaseNotice
from payments.models import Payment

from . import notices
from .models import NoticeLetter
from .services import tenancy, verify_url

CAPABILITY = notices.CAPABILITY
# The Small Claims Court Act 2016 s.12(3) limit, as raised by notice [VERIFY].
SMALL_CLAIMS_LIMIT = Decimal("1000000")
NOTE_MAX = 1000


class Purpose(models.TextChoices):
    ARREARS = "ARREARS", gettext_lazy("Rent arrears")
    MOVE_OUT = "MOVE_OUT", gettext_lazy("Disputed move-out")


def problem(lease: Lease) -> str:
    if lease.is_draft:
        return _("A draft lease has no tenancy to hand over.")
    return ""


def _check(actor: Membership, lease: Lease) -> None:
    if lease.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    if not can(actor, CAPABILITY, lease.unit.property):
        raise PermissionDenied(CAPABILITY)


def _day(day) -> str:
    return f"{day:%d %b %Y}" if day else ""


# ---------------------------------------------------------------------------
# What the pack says
# ---------------------------------------------------------------------------


def _tenants(head: Lease, sensitive: bool) -> list[dict]:
    links = sorted(head.lease_tenants.select_related("tenant"), key=lambda lt: (not lt.is_primary, lt.pk))
    out = []
    for lt in links:
        t = lt.tenant
        out.append({
            "name": t.name, "kind": t.get_kind_display(), "contact_person": t.contact_person,
            "phone": t.phone, "alt_phone": t.alt_phone, "email": t.email,
            "id": (f"{t.get_id_type_display()} {t.id_number}".strip() if t.id_number else "") if sensitive else None,
            "kra_pin": t.kra_pin if sensitive else None,
        })
    return out


def _letters(chain: list[Lease]) -> list[NoticeLetter]:
    return list(NoticeLetter.objects.filter(lease__in=chain).select_related("issued_by", "withdrawn_by", "proof")
                .order_by("issued_at", "pk"))


def _documents(membership: Membership, chain: list[Lease], head: Lease) -> list[Document]:
    from documents.services import visible_documents

    tenant_ids = head.lease_tenants.values("tenant_id")
    return list(visible_documents(membership).filter(Q(lease__in=chain) | Q(tenant__in=tenant_ids))
                .order_by("created_at", "pk"))


def _condition(head: Lease) -> list:
    from inspections.models import ConditionReport

    return list(ConditionReport.objects.filter(lease=head, status=ConditionReport.Status.COMPLETED)
                .select_related("inspected_by").order_by("inspected_on", "pk"))


def summary(membership: Membership, lease: Lease, *, purpose: str = Purpose.ARREARS,
            today: datetime.date | None = None) -> dict:
    """Everything the brief and the page show, from the records as they are now."""
    today = today or timezone.localdate()
    chain = tenancy(lease)
    head = chain[-1]
    prop = head.unit.property
    sensitive = can(membership, "tenants.view_sensitive")
    sees_payments = can(membership, "payments.view", prop)
    sees_condition = can(membership, "inspections.view", prop)

    entries = list(LedgerEntry.objects.filter(lease__in=chain).select_related("invoice", "payment")
                   .order_by("entry_date", "pk"))
    account = lease_arrears(head, entries, today)
    owed, _late = notices.arrears(chain, today)
    buckets = [(label, account.buckets[key]) for key, label, *_rest in AGING_BUCKETS if account.buckets[key]]
    held = DepositEntry.objects.filter(lease__in=chain).aggregate(s=Sum("amount"))["s"] or ZERO

    payments = Payment.objects.filter(lease__in=chain)
    confirmed = payments.filter(status=Payment.Status.CONFIRMED)
    last_payment = confirmed.order_by("-paid_at", "-pk").first()
    pending = payments.filter(status=Payment.Status.PENDING_REVIEW).aggregate(s=Sum("amount"), n=models.Count("pk"))

    letters = _letters(chain)
    follow_ups = list(FollowUp.objects.filter(lease__in=chain).select_related("created_by")
                      .order_by("created_at", "pk"))
    lease_notices = list(LeaseNotice.objects.filter(lease__in=chain).select_related("tenant")
                         .order_by("given_on", "pk"))
    documents = _documents(membership, chain, head)
    reports = _condition(head) if sees_condition else []
    move_out = paperwork.move_out(head, today) if paperwork.move_out_applies(head) else None

    data = {
        "purpose": purpose, "purpose_label": Purpose(purpose).label, "today": today,
        "organization": head.organization, "lease": head, "chain": chain, "property": prop, "unit": head.unit,
        "currency": head.currency, "tenants": _tenants(head, sensitive), "sensitive": sensitive,
        "rent": head.current_rent, "deposit_held": held,
        "balance": account.balance, "owed": owed, "debts": account.debts, "buckets": buckets,
        "late_from": account.late_from, "days_overdue": account.days_overdue(today),
        "sees_payments": sees_payments, "sees_condition": sees_condition,
        "last_payment": last_payment if sees_payments else None,
        "pending_total": pending["s"] or ZERO, "pending_count": pending["n"],
        "letters": letters, "follow_ups": follow_ups, "notices": lease_notices, "documents": documents,
        "reports": reports, "move_out": move_out, "entries": entries,
    }
    data["concerns"] = concerns(data)
    return data


def concerns(data: dict) -> list[str]:
    """What to look at before handing the matter over [VERIFY with counsel]."""
    out = []
    cur, today = data["currency"], data["today"]
    live = [x for x in data["letters"] if not x.is_withdrawn]
    demands = [x for x in live if x.kind == NoticeLetter.Kind.DEMAND]
    if data["purpose"] == Purpose.ARREARS:
        if data["owed"] <= 0:
            out.append(_("Nothing past its grace days is owed on this tenancy."))
        if not any(d.is_served for d in demands):
            out.append(_("No demand letter has been recorded as served on this tenancy. Advocates usually want "
                         "proof that payment was asked for, or send their own demand first."))
        for d in demands:
            if d.pay_by and d.pay_by >= today:
                out.append(_("Demand %(number)s was served on %(served)s; its days run until %(day)s.")
                           % {"number": d.number, "served": _day(d.served_on), "day": _day(d.pay_by)})
        if cur == "KES" and data["owed"] > SMALL_CLAIMS_LIMIT:
            out.append(_("The amount is above %(limit)s, the Small Claims Court limit [VERIFY].")
                       % {"limit": format_money(SMALL_CLAIMS_LIMIT, cur)})
        if data["deposit_held"] > 0:
            out.append(_("A deposit of %(amount)s is still held. It may have to be set off against the arrears "
                         "when the tenancy ends.") % {"amount": format_money(data["deposit_held"], cur)})
    else:
        if data["move_out"] is None:
            out.append(_("No notice has been given and the lease has not ended, so there is no move-out "
                         "statement yet."))
        if data["sees_condition"]:
            kinds = {r.kind for r in data["reports"]}
            if "MOVE_OUT" not in kinds:
                out.append(_("There is no completed move-out condition report."))
            if "MOVE_IN" not in kinds:
                out.append(_("There is no completed move-in condition report on this lease to compare with."))
    latest = data["follow_ups"][-1] if data["follow_ups"] else None
    if latest and latest.outcome == FollowUp.Outcome.DISPUTED:
        out.append(_("The latest follow-up says the tenant disputes the amount."))
    if latest and latest.outcome == FollowUp.Outcome.PROMISED and latest.promised_on >= today:
        out.append(_("The tenant promised to pay by %(day)s.") % {"day": _day(latest.promised_on)})
    if data["pending_count"]:
        out.append(_("Payments of %(amount)s are waiting for review; the balance falls once they are confirmed.")
                   % {"amount": format_money(data["pending_total"], cur)})
    if data["property"].category in notices.BUSINESS:
        out.append(_("This is a commercial or mixed-use property. A business tenancy may fall under Cap. 301 and "
                     "the Business Premises Rent Tribunal [VERIFY]."))
    if not any(d.category == Document.Category.LEASE for d in data["documents"]):
        out.append(_("No signed tenancy agreement is filed with the lease's documents."))
    if data["sensitive"] and any(not t["id"] for t in data["tenants"]):
        out.append(_("A tenant has no ID or registration number on file; an advocate needs it to name them in "
                     "a claim."))
    if not data["sensitive"]:
        out.append(_("ID numbers, KRA PINs and tenant documents are left out because you cannot see them."))
    if not data["sees_payments"]:
        out.append(_("The payments list is left out because you cannot see payments."))
    return out


# ---------------------------------------------------------------------------
# Rows shared by the PDF and the CSV
# ---------------------------------------------------------------------------


def describe(entry: LedgerEntry) -> str:
    parts = [entry.get_kind_display()]
    if entry.invoice_id:
        parts.append(entry.invoice.number)
        if entry.kind == LedgerEntry.Kind.INVOICE and entry.invoice.period_label:
            parts.append(entry.invoice.period_label)
    if entry.payment_id:
        p = entry.payment
        parts.append(" ".join(x for x in (p.get_method_display(), p.reference) if x))
    if entry.reason:
        parts.append(entry.reason)
    return " · ".join(parts)


def statement_rows(entries: list[LedgerEntry]) -> list[dict]:
    rows, balance = [], ZERO
    for e in entries:
        balance += e.amount
        rows.append({"date": e.entry_date, "lease": e.lease_id, "text": describe(e),
                     "debit": e.amount if e.amount > 0 else None, "credit": -e.amount if e.amount < 0 else None,
                     "balance": balance})
    return rows


def _cell(value) -> str:
    """Text a spreadsheet will not run as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv(header: list[str], rows) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in rows:
        writer.writerow([v if isinstance(v, Decimal | int) else _cell(v) for v in row])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def _statement_csv(data: dict) -> bytes:
    numbers = {lease.pk: lease.number for lease in data["chain"]}
    return _csv([_("Date"), _("Lease"), _("Details"), _("Debit"), _("Credit"), _("Balance")],
                ([r["date"].isoformat(), numbers.get(r["lease"], ""), r["text"], r["debit"] or "",
                  r["credit"] or "", r["balance"]] for r in statement_rows(data["entries"])))


def _payments_csv(chain: list[Lease]) -> bytes:
    payments = (Payment.objects.filter(lease__in=chain).select_related("lease", "tenant", "recorded_by",
                                                                       "confirmed_by", "payment_account")
                .order_by("paid_at", "pk"))
    name = lambda user: (user.get_full_name() or user.get_username()) if user else ""  # noqa: E731
    return _csv([_("Date paid"), _("Lease"), _("Amount"), _("Method"), _("Reference"), _("Paid by"), _("Status"),
                 _("Recorded by"), _("Confirmed by"), _("Confirmed at"), _("Reversal reason")],
                ([p.paid_at.isoformat(), p.lease.number, p.amount, p.get_method_display(), p.reference,
                  p.tenant.name if p.tenant_id else "", p.get_status_display(), name(p.recorded_by),
                  name(p.confirmed_by), timezone.localtime(p.confirmed_at).isoformat() if p.confirmed_at else "",
                  p.reversal_reason] for p in payments))


def _follow_ups_csv(follow_ups: list[FollowUp]) -> bytes:
    return _csv([_("When"), _("Outcome"), _("Note"), _("Will pay by"), _("Amount promised"), _("By")],
                ([timezone.localtime(f.created_at).isoformat(timespec="minutes"), f.get_outcome_display(), f.note,
                  f.promised_on.isoformat() if f.promised_on else "", f.promised_amount or "",
                  (f.created_by.get_full_name() or f.created_by.get_username()) if f.created_by_id else ""]
                 for f in follow_ups))


# ---------------------------------------------------------------------------
# The pack
# ---------------------------------------------------------------------------


def _safe_name(text: str, limit: int = 60) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")
    return (text or "file")[:limit]


def _read(field) -> bytes:
    with field.open("rb") as f:
        return f.read()


def _extension(name: str, default: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else default


def build(actor: Membership, lease: Lease, *, purpose: str = Purpose.ARREARS, advocate: str = "", note: str = "",
          request=None) -> tuple[str, bytes]:
    """The ZIP and its file name. Audited with the pack's SHA-256 and the amount it states."""
    from . import handover_pdf as pdf

    _check(actor, lease)
    if purpose not in Purpose.values:
        raise ValidationError({"purpose": _("Choose what the pack is for.")})
    reason = problem(lease)
    if reason:
        raise ValidationError(reason)
    data = summary(actor, lease, purpose=purpose)
    head = data["lease"]
    data["advocate"] = (advocate or "").strip()[:150]
    data["note"] = (note or "").strip()[:NOTE_MAX]
    data["prepared_by"] = actor.user.get_full_name() or actor.user.get_username()
    data["prepared_at"] = timezone.now()

    files: dict[str, bytes] = {}
    files["01-statement.pdf"] = pdf.render_statement(data)
    files["01-statement.csv"] = _statement_csv(data)
    if data["sees_payments"]:
        files["02-payments.csv"] = _payments_csv(data["chain"])
    if data["follow_ups"]:
        files["03-follow-ups.csv"] = _follow_ups_csv(data["follow_ups"])
    visible_docs = {d.pk for d in data["documents"]}
    for letter in data["letters"]:
        if letter.pdf:
            files[f"04-letters/{letter.number}.pdf"] = _read(letter.pdf)
        if letter.proof_id and letter.proof_id in visible_docs:
            ext = _extension(letter.proof.filename, "pdf")
            files[f"04-letters/{letter.number}-as-served.{ext}"] = _read(letter.proof.file)
    if data["move_out"] is not None:
        from leases.pdf import render_move_out

        files["05-move-out-statement.pdf"] = render_move_out(data["move_out"])
    if data["reports"]:
        files["06-condition/condition-reports.pdf"] = pdf.render_condition(data)
        for report in data["reports"]:
            for n, photo in enumerate(report.photos.select_related("line").order_by("uploaded_at", "pk"), 1):
                label = _safe_name(str(photo.line) if photo.line_id else "unit", 40)
                ext = _extension(photo.image.name, "jpg")
                files[f"06-condition/{report.kind.lower()}-{n:02d}-{label}.{ext}"] = _read(photo.image)
    proofs = {x.proof_id for x in data["letters"] if x.proof_id}
    for n, doc in enumerate((d for d in data["documents"] if d.pk not in proofs), 1):
        ext = _extension(doc.filename, "pdf")
        files[f"07-documents/{n:02d}-{_safe_name(doc.title)}.{ext}"] = _read(doc.file)

    fingerprints = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
    data["fingerprints"] = fingerprints
    brief = pdf.render_brief(data)
    fingerprints = {"00-brief.pdf": hashlib.sha256(brief).hexdigest(), **fingerprints}
    record = _record(actor, data, fingerprints)
    record_bytes = json.dumps(record, indent=2, ensure_ascii=False, default=str).encode("utf-8")
    certificate = pdf.render_certificate(data, {**fingerprints,
                                                "record.json": hashlib.sha256(record_bytes).hexdigest()})

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as pack:
        pack.writestr("00-brief.pdf", brief)
        for name, content in files.items():
            pack.writestr(name, content)
        pack.writestr("record.json", record_bytes)
        pack.writestr("certificate.pdf", certificate)
    content = out.getvalue()
    audit.record("handover.pack", actor=actor.user, organization=actor.organization, obj=head, request=request,
                 changes={"purpose": [None, purpose], "owed": [None, str(data["owed"])],
                          "balance": [None, str(data["balance"])], "advocate": [None, data["advocate"] or None],
                          "files": [None, len(fingerprints) + 2],
                          "sha256": [None, hashlib.sha256(content).hexdigest()]})
    filename = f"{_safe_name(head.number or str(head.public_id))}-handover-{data['today']:%Y%m%d}.zip"
    return filename, content


def _record(actor: Membership, data: dict, fingerprints: dict) -> dict:
    """The facts behind the pack, for a machine or a second look: fingerprints, letters and their audit trail."""
    head = data["lease"]
    letter_ids = [str(x.public_id) for x in data["letters"]]
    events = (AuditEvent.objects.filter(organization=actor.organization, object_type="letters.noticeletter",
                                        object_id__in=letter_ids).select_related("actor").order_by("created_at", "pk"))
    when = lambda moment: timezone.localtime(moment).isoformat() if moment else None  # noqa: E731
    return {
        "purpose": data["purpose"], "prepared_at": when(data["prepared_at"]), "prepared_by": data["prepared_by"],
        "advocate": data["advocate"], "organization": head.organization.name,
        "property": data["property"].name, "address": data["property"].address_line, "unit": data["unit"].code,
        "leases": [{"number": x.number, "status": x.status, "start": x.start_date, "end": x.end_date,
                    "ended_on": x.ended_on} for x in data["chain"]],
        "tenants": data["tenants"], "currency": data["currency"],
        "balance": str(data["balance"]), "owed_past_grace": str(data["owed"]),
        "deposit_held": str(data["deposit_held"]),
        "unpaid": [{"due": d.due, "late_from": d.late_from, "amount": str(d.amount),
                    "invoice": d.invoice.number if d.invoice else None} for d in data["debts"]],
        "letters": [{"number": x.number, "kind": x.kind, "issued_at": when(x.issued_at),
                     "served_on": x.served_on, "served_via": x.served_via, "served_by": x.served_by_name,
                     "received_by": x.received_by, "note": x.service_note, "pay_by": x.pay_by,
                     "withdrawn_at": when(x.withdrawn_at), "withdraw_reason": x.withdraw_reason,
                     "check_url": verify_url(x)} for x in data["letters"]],
        "letters_audit": [{"at": when(e.created_at), "action": e.action, "object": e.object_repr,
                           "by": e.actor.get_full_name() if e.actor_id else None, "changes": e.changes}
                          for e in events],
        "files": fingerprints,
    }
