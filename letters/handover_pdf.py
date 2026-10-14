"""The handover pack's own PDFs (D-122): the brief, the statement of account, the condition reports side by side,
and the Evidence Act s.106B certificate. The facts come from handover.summary, so the page and the pack agree.
"""

import datetime
import io

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

from core.money import format_money
from leases.pdf import BLANK, HEAD, LABEL, LINE, RIGHT, RIGHT_BOLD, SECTION, SMALL, TITLE, _doc, _facts, _p

from .handover import statement_rows

MONO = ParagraphStyle("mono", parent=SMALL, fontName="Courier", fontSize=6.5, leading=9)


def _day(day) -> str:
    if isinstance(day, datetime.datetime):
        day = timezone.localdate(day)
    return f"{day:%d %b %Y}" if day else ""


def _when(moment) -> str:
    return f"{timezone.localtime(moment):%d %b %Y %H:%M}" if moment else ""


def _grid(head: list[str], rows: list[list], widths: list, *, right: tuple = (), bold_last: bool = False) -> Table:
    def cell(value, col, last):
        style = (RIGHT_BOLD if last else RIGHT) if col in right else (LABEL if last else None)
        return _p(value, style) if style else _p(value)

    body = [[cell(v, c, bold_last and i == len(rows) - 1) for c, v in enumerate(row)] for i, row in enumerate(rows)]
    table = Table([[_p(h, LABEL) for h in head], *body], colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def _fingerprints(fingerprints: dict) -> Table:
    """File names and their SHA-256 in small type, so each fingerprint stays on one line."""
    table = Table([[_p(_("File"), LABEL), _p(_("SHA-256"), LABEL)]]
                  + [[_p(name, SMALL), _p(sha, MONO)] for name, sha in fingerprints.items()],
                  colWidths=[68 * mm, None], repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def _header(data: dict, title: str) -> list:
    return [_p(data["organization"].display_name, HEAD), Spacer(1, 2 * mm),
            _p(_("Prepared %(when)s by %(who)s") % {"when": _when(data["prepared_at"]), "who": data["prepared_by"]},
               SMALL),
            Spacer(1, 5 * mm), _p(title, TITLE), Spacer(1, 3 * mm)]


def _premises(data: dict) -> str:
    prop = data["property"]
    return ", ".join(x for x in (_("Unit %(unit)s") % {"unit": data["unit"].code}, prop.name, prop.address_line) if x)


# ---------------------------------------------------------------------------
# Brief
# ---------------------------------------------------------------------------


def render_brief(data: dict) -> bytes:
    money = lambda v: format_money(v, data["currency"])  # noqa: E731
    head = data["lease"]
    buf = io.BytesIO()
    doc = _doc(buf, f"{_('Handover brief')} {head.number}")
    story = _header(data, _("HANDOVER BRIEF: %(purpose)s") % {"purpose": str(data["purpose_label"]).upper()})
    org = data["organization"]
    story.append(_facts([
        (_("Prepared for"), data["advocate"] or BLANK),
        (_("Landlord"), " · ".join(x for x in (org.display_name, org.billing_phone,
                                               org.reply_email or org.billing_email) if x)),
        (_("Premises"), _premises(data)),
        (_("Kind of property"), data["property"].get_category_display()),
        (_("Figures as at"), _day(data["today"])),
    ]))
    if data["note"]:
        story += [_p(_("Note from the landlord"), SECTION), _p(data["note"])]

    story.append(_p(_("Tenants"), SECTION))
    for t in data["tenants"]:
        rows = [(_("Name"), t["name"] + (f" ({t['kind']})" if t["kind"] else ""))]
        if t["contact_person"]:
            rows.append((_("Contact person"), t["contact_person"]))
        rows.append((_("Phone"), ", ".join(x for x in (t["phone"], t["alt_phone"]) if x)))
        if t["email"]:
            rows.append((_("Email"), t["email"]))
        if t["id"] is not None:
            rows += [(_("ID / registration"), t["id"] or _("Not on file")), (_("KRA PIN"), t["kra_pin"] or "—")]
        story.append(_facts(rows))

    story.append(_p(_("The lease"), SECTION))
    lease_rows = []
    for lease in data["chain"]:
        span = _("%(start)s to %(end)s") % {"start": _day(lease.start_date),
                                            "end": _day(lease.ended_on or lease.end_date) or _("open-ended")}
        lease_rows.append((lease.number, f"{lease.get_status_display()} · {span}"))
    lease_rows += [
        (_("Rent now"), money(data["rent"]) + " " + _("a month") if data["rent"] is not None else "—"),
        (_("Due day and grace"), _("Day %(day)s, %(grace)s days' grace") % {"day": head.due_day,
                                                                             "grace": head.grace_days}),
        (_("Notice period"), _("%(days)s days") % {"days": head.notice_days}),
        (_("Deposit held"), money(data["deposit_held"])),
    ]
    if head.notice_given_on:
        lease_rows.append((_("Notice given"), _("%(given)s, to move out by %(day)s")
                           % {"given": _day(head.notice_given_on), "day": _day(head.move_out_on)}))
    if head.ended_on:
        lease_rows.append((_("Ended"), f"{_day(head.ended_on)} · {head.end_reason}".strip(" ·")))
    story.append(_facts(lease_rows))

    story.append(_p(_("The amount"), SECTION))
    amount_rows = [
        (_("Balance on the account"), money(data["balance"])),
        (_("Owed past its grace days"), money(data["owed"])),
    ]
    if data["late_from"]:
        amount_rows.append((_("Oldest unpaid amount late since"),
                            _("%(day)s (%(n)s days)") % {"day": _day(data["late_from"]), "n": data["days_overdue"]}))
    if data["last_payment"]:
        p = data["last_payment"]
        amount_rows.append((_("Last payment"), f"{_day(p.paid_at)} · {money(p.amount)} · "
                                               f"{p.get_method_display()} {p.reference}".strip()))
    if data["buckets"]:
        amount_rows.append((_("How late"), [_p(f"{label}: {money(value)}") for label, value in data["buckets"]]))
    story.append(_facts(amount_rows))
    if data["debts"]:
        rows = [[d.invoice.number if d.invoice else _("Other item"), _day(d.due), _day(d.late_from),
                 money(d.amount)] for d in data["debts"][:40]]
        if len(data["debts"]) > 40:
            rows.append([_("and %(n)s more, in the statement") % {"n": len(data["debts"]) - 40}, "", "", ""])
        story += [Spacer(1, 2 * mm), _p(_("What is unpaid, oldest first"), LABEL),
                  _grid([_("Item"), _("Due"), _("Late from"), _("Unpaid")], rows, [None, 28 * mm, 28 * mm, 34 * mm],
                        right=(3,))]

    story.append(_p(_("Notices and demands"), SECTION))
    if data["letters"]:
        rows = []
        for x in data["letters"]:
            if x.is_withdrawn:
                served = _("Withdrawn %(day)s: %(reason)s") % {"day": _day(x.withdrawn_at), "reason": x.withdraw_reason}
            elif x.is_served:
                served = _("Served %(day)s, %(how)s") % {"day": _day(x.served_on),
                                                         "how": x.get_served_via_display().lower()}
                if x.received_by:
                    served += "; " + _("received by %(name)s") % {"name": x.received_by}
                if x.pay_by:
                    served += "; " + _("pay by %(day)s") % {"day": _day(x.pay_by)}
                if x.proof_id:
                    served += "; " + _("signed copy enclosed")
            else:
                served = _("Not recorded as served")
            rows.append([x.number, x.get_kind_display(), _day(x.issued_at), served])
        story.append(_grid([_("Number"), _("Letter"), _("Issued"), _("Service")], rows,
                           [37 * mm, 36 * mm, 24 * mm, None]))
    else:
        story.append(_p(_("No notice or demand letter has been issued from the app."), SMALL))
    if data["notices"]:
        story += [Spacer(1, 2 * mm), _p(_("Notices recorded on the lease"), LABEL),
                  _grid([_("Given by"), _("Given on"), _("Move out by"), _("Status")],
                        [[n.get_given_by_display(), _day(n.given_on), _day(n.move_out_on),
                          _("Withdrawn") if n.withdrawn_at else _("Open")] for n in data["notices"]],
                        [40 * mm, 30 * mm, 30 * mm, None])]

    story.append(_p(_("Follow-ups"), SECTION))
    if data["follow_ups"]:
        rows = []
        for f in data["follow_ups"][-30:]:
            text = f.note
            if f.promised_on:
                text = (_("Will pay %(amount)s by %(day)s.") % {"amount": money(f.promised_amount) if f.promised_amount
                                                                 else "", "day": _day(f.promised_on)}
                        + (" " + text if text else "")).replace("  ", " ")
            rows.append([_day(f.created_at), f.get_outcome_display(), text])
        story.append(_grid([_("When"), _("Outcome"), _("Note")], rows, [26 * mm, 38 * mm, None]))
        if len(data["follow_ups"]) > 30:
            story.append(_p(_("The latest 30 are shown; all are in 03-follow-ups.csv."), SMALL))
    else:
        story.append(_p(_("No calls or visits about money owed were recorded."), SMALL))

    if data["concerns"]:
        story.append(_p(_("To check before acting"), SECTION))
        story += [_p("• " + c) for c in data["concerns"]]

    story += [_p(_("What is enclosed"), SECTION),
              _fingerprints(data["fingerprints"]),
              Spacer(1, 4 * mm),
              _p(_("This brief is drawn from the landlord's records in NyumbaPMS as they stood when it was prepared. "
                   "It is not legal advice. Payments made or recorded after that time are not included. "
                   "certificate.pdf is to be completed and signed by a person responsible for the records."), SMALL)]
    doc.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Statement of account
# ---------------------------------------------------------------------------


def render_statement(data: dict) -> bytes:
    money = lambda v: format_money(v, data["currency"]) if v is not None else ""  # noqa: E731
    head = data["lease"]
    numbers = {lease.pk: lease.number for lease in data["chain"]}
    buf = io.BytesIO()
    doc = _doc(buf, f"{_('Statement of account')} {head.number}")
    story = _header(data, _("STATEMENT OF ACCOUNT"))
    story.append(_facts([
        (_("Tenants"), ", ".join(t["name"] for t in data["tenants"]) or "—"),
        (_("Premises"), _premises(data)),
        (_("Leases"), ", ".join(lease.number for lease in data["chain"])),
        (_("As at"), _day(data["today"])),
    ]))
    rows = [[_day(r["date"]), (numbers.get(r["lease"], "") + " · " if len(data["chain"]) > 1 else "") + r["text"],
             money(r["debit"]), money(r["credit"]), money(r["balance"])] for r in statement_rows(data["entries"])]
    rows.append([_day(data["today"]), _("Balance"), "", "", money(data["balance"])])
    story += [Spacer(1, 4 * mm),
              _grid([_("Date"), _("Details"), _("Debit"), _("Credit"), _("Balance")], rows,
                    [22 * mm, None, 27 * mm, 27 * mm, 29 * mm], right=(2, 3, 4), bold_last=True),
              Spacer(1, 3 * mm),
              _p(_("A debit is an amount billed to the tenant; a credit is a payment or other amount in their "
                   "favour. Every row comes from the lease's ledger, which is never edited: a mistake is cancelled "
                   "by a later correction row."), SMALL)]
    doc.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Condition reports
# ---------------------------------------------------------------------------


def render_condition(data: dict) -> bytes:
    from inspections import services as inspections

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title=f"{_('Condition reports')} {data['lease'].number}")
    story = _header(data, _("CONDITION REPORTS"))
    for report in data["reports"]:
        before = inspections.baseline(report)
        story += [_p(f"{report.get_kind_display()} · {_day(report.inspected_on)}", SECTION), _facts([
            (_("Inspected by"), report.inspected_by.get_full_name() or report.inspected_by.get_username()),
            (_("Tenant present"), _("Yes") if report.tenant_present else _("No")),
            (_("Keys handed over"), "" if report.keys_handed is None else str(report.keys_handed)),
            (_("Tenant's comments"), report.tenant_comments or "—"),
            (_("Notes"), report.notes or "—"),
            (_("Completed"), _when(report.completed_at)),
        ])]
        rows = []
        for r in inspections.rows(report, before):
            line, prev = r["line"], r["before"]
            was = f"{prev.get_condition_display()} {prev.notes}".strip() if prev else ""
            now = f"{line.get_condition_display()} {line.notes}".strip()
            rows.append([str(line), was, now, _("Worse") if r["worse"] else "", str(len(r["photos"]) or "")])
        if rows:
            head = [_("Item"), _("At move-in") if before else "", _("This report"), "", _("Photos")]
            story += [Spacer(1, 2 * mm), _grid(head, rows, [70 * mm, 70 * mm, 70 * mm, 20 * mm, None])]
    story += [Spacer(1, 3 * mm),
              _p(_("Photos are in this folder, named by report, number and item."), SMALL)]
    doc.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Certificate
# ---------------------------------------------------------------------------


def render_certificate(data: dict, fingerprints: dict) -> bytes:
    """Evidence Act (Cap. 80) s.106B(4): identifies the records, says how they were produced and covers the
    s.106B(2) conditions. A person in a responsible position fills in the blanks and signs it [VERIFY]."""
    org = data["organization"].display_name
    head = data["lease"]
    buf = io.BytesIO()
    doc = _doc(buf, f"{_('Certificate')} {head.number}")
    story = [
        _p(org, HEAD), Spacer(1, 6 * mm),
        _p(_("CERTIFICATE UNDER SECTION 106B(4) OF THE EVIDENCE ACT (CAP. 80)"), TITLE), Spacer(1, 3 * mm),
        _p(_("1. The records. This certificate concerns the electronic records below, about the tenancy of "
             "%(premises)s under lease %(lease)s, prepared on %(when)s.")
           % {"premises": _premises(data), "lease": head.number, "when": _when(data["prepared_at"])}),
        Spacer(1, 2 * mm),
        _fingerprints(fingerprints),
        Spacer(1, 3 * mm),
        _p(_("2. How they were produced. The records were produced by NyumbaPMS, a web application run for %(org)s "
             "at %(site)s, from the information stored in it about the tenancy: the lease, the account of what was "
             "billed and paid, payments, calls and visits about money owed, letters issued and how they were "
             "served, condition reports, and documents uploaded. The account is kept as rows that the application "
             "does not edit or delete; a mistake is cancelled by a later row. Letters were stored as PDFs when they "
             "were issued and were not changed afterwards. Each step taken in the application is written to an "
             "audit trail that it cannot edit or delete.") % {"org": org, "site": settings.SITE_URL}),
        Spacer(1, 3 * mm),
        _p(_("3. Conditions. The records were produced in the ordinary course of %(org)s's management of the "
             "tenancy, during a period when the system was used regularly to store and process such information. "
             "Throughout that period information of this kind was regularly supplied to the system in the "
             "ordinary course of those activities. To the best of my knowledge the system was operating properly "
             "throughout; if at any time it was not, that did not affect the production of these records or "
             "their accuracy. The records reproduce the information as it was supplied to the system.")
           % {"org": org}),
        Spacer(1, 3 * mm),
        _p(_("4. Checking a copy. A file whose SHA-256 differs from the one shown above has been changed. Each "
             "letter also carries a link that shows, without a login, the facts it stated when it was issued.")),
        Spacer(1, 3 * mm),
        _p(_("To the best of my knowledge and belief, the matters stated in this certificate are true.")),
        Spacer(1, 8 * mm),
        _facts([
            (_("Name"), BLANK * 2),
            (_("Position (in relation to the system or the management of the tenancy)"), BLANK * 2),
            (_("Signature"), BLANK * 2),
            (_("Date"), BLANK * 2),
        ]),
    ]
    doc.build(story)
    return buf.getvalue()
