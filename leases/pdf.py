"""The lease agreement and move-out statement as A4 PDFs, drawn with ReportLab like letters (D-048, D-073).

Everything printed comes from leases.paperwork, so the move-out page and its PDF say the same.
"""

import io
from xml.sax.saxutils import escape

from django.utils.translation import gettext as _
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, Spacer, Table, TableStyle

from core import brand
from core.money import format_money

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=14, textColor=brand.INK)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=11, textColor=brand.MUTED)
LABEL = ParagraphStyle("label", parent=BODY, fontName="Helvetica-Bold")
RIGHT = ParagraphStyle("right", parent=BODY, alignment=2)
RIGHT_BOLD = ParagraphStyle("right_bold", parent=RIGHT, fontName="Helvetica-Bold")
HEAD = ParagraphStyle("head", parent=BODY, fontName="Helvetica-Bold", fontSize=15, leading=19)
TITLE = ParagraphStyle("title", parent=BODY, fontName="Helvetica-Bold", fontSize=12, leading=16, textColor=brand.PRIMARY,
                       keepWithNext=1)
SECTION = ParagraphStyle("section", parent=BODY, fontName="Helvetica-Bold", fontSize=11, leading=15,
                         spaceBefore=8, spaceAfter=3, textColor=brand.PRIMARY, keepWithNext=1)
DRAFT = ParagraphStyle("draft", parent=TITLE, textColor=brand.WARN)

LINE = brand.LINE
BLANK = "_" * 28


def _p(text, style=BODY):
    return Paragraph(escape(str(text)), style)


def _day(day) -> str:
    return f"{day:%d %b %Y}" if day else ""


def _doc(buf, title):
    return brand.Doc(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                             bottomMargin=18 * mm, title=title)


def _facts(rows) -> Table:
    """Label and value rows, as on letters."""
    table = Table([[_p(label, LABEL), value if isinstance(value, list) else _p(value)] for label, value in rows],
                  colWidths=[45 * mm, None])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def _amounts(rows, total=None) -> Table:
    """Description and amount rows, with an optional bold total line."""
    data = [[_p(label), _p(amount, RIGHT)] for label, amount in rows]
    if total:
        data.append([_p(total[0], LABEL), _p(total[1], RIGHT_BOLD)])
    table = Table(data, colWidths=[None, 45 * mm])
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]
    if total:
        style.append(("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black))
    table.setStyle(TableStyle(style))
    return table


def _signatures(heading, parties) -> KeepTogether:
    """A heading, then a signature, name and date line for each party, kept on one page."""
    rows = []
    for role, name in parties:
        rows.append([_p(role, LABEL), _p(name or "")])
        rows.append([_p(_("Signature")), _p(BLANK)])
        rows.append([_p(_("Date")), _p(BLANK)])
    table = Table(rows, colWidths=[45 * mm, None])
    table.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        *[("TOPPADDING", (0, i), (-1, i), 10) for i in range(0, len(rows), 3) if i],
    ]))
    return KeepTogether([Spacer(1, 6 * mm), _p(heading, SECTION), table])


def _signed_electronically(heading, parties) -> KeepTogether:
    """In place of signature lines on a copy sent for e-signature (D-118): who signs, and where it is recorded."""
    rows = [[_p(role, LABEL), _p(name or "")] for role, name in parties]
    table = Table(rows, colWidths=[45 * mm, None])
    table.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return KeepTogether([Spacer(1, 6 * mm), _p(heading, SECTION), table, Spacer(1, 2 * mm), _p(_(
        "Signed electronically. Who signed, when and how each signer was checked is set out in the signing "
        "record at the end of this document."), SMALL)])


def _signature_block(data, heading, parties):
    if data.get("for_signing"):
        return _signed_electronically(heading, parties)
    return _signatures(heading, parties)


def _head(org, name, kind, number=""):
    """The letterhead every document starts with: logo, name and contact on the left, what it is on the right."""
    return brand.letterhead(name, org=org, kind=kind, number=number, lines=brand.org_lines(org))


def _footer(story, org):
    if org.document_footer:
        story += [Spacer(1, 6 * mm), _p(org.document_footer, SMALL)]


# ---------------------------------------------------------------------------
# Agreement
# ---------------------------------------------------------------------------


def render_agreement(data: dict) -> bytes:
    lease, money = data["lease"], (lambda v: format_money(v, data["currency"]))
    buf = io.BytesIO()
    doc = _doc(buf, f"{_('Tenancy agreement')} {lease.number or _('draft')}")
    org = lease.organization
    story = [_head(org, data["organization"], _("Tenancy agreement"), lease.number or "")]
    if data["draft"] and data.get("for_signing"):
        story += [Spacer(1, 2 * mm), _p(_("For signing – in force from the start date once signed by all parties"), SMALL)]
    elif data["draft"]:
        story += [Spacer(1, 2 * mm), _p(_("DRAFT – not yet in force"), DRAFT)]
    story += [Spacer(1, 6 * mm), _p(_("TENANCY AGREEMENT"), TITLE), Spacer(1, 3 * mm),
              _p(_("This agreement is made between the landlord and the tenant(s) named below, "
                   "for the premises and on the terms set out here.")), Spacer(1, 4 * mm)]

    story.append(_p(_("1. The parties"), SECTION))
    landlord = [_p(data["organization"])]
    if data["organization_pin"]:
        landlord.append(_p(_("KRA PIN %(pin)s") % {"pin": data["organization_pin"]}))
    rows = [(_("Landlord"), landlord)]
    for i, t in enumerate(data["tenants"], start=1):
        lines = [_p(t["name"]), _p(t["phone"])]
        if t["email"]:
            lines.append(_p(t["email"]))
        if t["id"] is not None:
            lines.append(_p(_("ID: %(id)s") % {"id": t["id"] or BLANK}))
            lines.append(_p(_("KRA PIN: %(pin)s") % {"pin": t["kra_pin"] or BLANK}))
        else:
            lines += [_p(_("ID: %(id)s") % {"id": BLANK}), _p(_("KRA PIN: %(pin)s") % {"pin": BLANK})]
        rows.append((_("Tenant") if len(data["tenants"]) == 1 else _("Tenant %(n)s") % {"n": i}, lines))
    story.append(_facts(rows))

    story.append(_p(_("2. The premises"), SECTION))
    rows = [(_("Property"), [_p(data["property"])] + ([_p(data["address"])] if data["address"] else [])),
            (_("Unit"), f"{data['unit']} · {data['unit_label']}")]
    story.append(_facts(rows))

    story.append(_p(_("3. Term"), SECTION))
    if data["end"]:
        term = _("From %(start)s to %(end)s.") % {"start": _day(data["start"]), "end": _day(data["end"])}
    else:
        term = _("From %(start)s, periodic: it runs until either party ends it by notice.") % {
            "start": _day(data["start"])}
    story.append(_facts([
        (_("Term"), term),
        (_("Notice"), _("%(days)s days' written notice by either party.") % {"days": data["notice_days"]}),
    ]))

    story.append(_p(_("4. Rent and deposit"), SECTION))
    rows = []
    if data["rent"] is not None:
        rent = [_p(_("%(amount)s a month") % {"amount": money(data["rent"])})]
        if data["months"] > 1:
            rent.append(_p(_("Billed %(frequency)s: %(amount)s per invoice") % {
                "frequency": data["frequency"].lower(), "amount": money(data["rent_per_period"])}))
        for change in data["later_rents"]:
            rent.append(_p(_("%(amount)s a month from %(day)s") % {"amount": money(change.amount),
                                                                    "day": _day(change.effective_from)}))
        rows.append((_("Rent"), rent))
    rows.append((_("Due"), _("On day %(day)s of the month, late after %(grace)s days' grace.") % {
        "day": data["due_day"], "grace": data["grace_days"]}))
    for charge in data["charges"]:
        until = _(" until %(day)s") % {"day": _day(charge.active_to)} if charge.active_to else ""
        rows.append((str(charge.charge_type), _("%(amount)s a month%(until)s") % {
            "amount": money(charge.amount), "until": until}))
    rows.append((_("Deposit"), money(data["deposit"])))
    if data["payers"]:
        rows.append((_("Also paying"), [_p(f"{p.phone}{' · ' + p.name if p.name else ''}")
                                        for p in data["payers"]]))
    story.append(_facts(rows))

    if data["terms"]:
        story.append(_p(_("5. Other terms"), SECTION))
        story += [_p(line) if line.strip() else Spacer(1, 2 * mm) for line in data["terms"].splitlines()]

    story += [Spacer(1, 6 * mm), _p(_(
        "Prepared from the lease record. It contains only the terms recorded on the lease; "
        "the parties should have their agreement reviewed before signing."), SMALL)]
    parties = [(_("For the landlord"), data["organization"])]
    parties += [(_("Tenant"), t["name"]) for t in data["tenants"]]
    if not data.get("for_signing"):
        parties.append((_("Witness"), ""))
    story.append(_signature_block(data, _("Signed by the parties"), parties))
    _footer(story, lease.organization)
    doc.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Move-out statement
# ---------------------------------------------------------------------------


def settlement_text(due) -> str:
    if due > 0:
        return _("Due to the tenant")
    if due < 0:
        return _("Owed by the tenant")
    return _("Nothing due either way")


def render_move_out(data: dict) -> bytes:
    lease, money = data["lease"], (lambda v: format_money(v, data["currency"]))
    buf = io.BytesIO()
    doc = _doc(buf, f"{_('Move-out statement')} {lease.number}")
    story = [_head(lease.organization, data["organization"], _("Move-out statement"), lease.number or ""),
             Spacer(1, 2 * mm), _p(_("As at %(day)s") % {"day": _day(data["today"])}, SMALL), Spacer(1, 6 * mm),
             _p(_("MOVE-OUT STATEMENT"), TITLE), Spacer(1, 3 * mm)]
    moved = _day(data["moved_out"])
    rows = [
        (_("Tenant"), [_p(t.name) for t in data["tenants"]]),
        (_("Premises"), f"{data['property']} · {data['unit']}"),
        (_("Lease"), f"{lease.number} · {_('from')} {_day(lease.start_date)}"),
    ]
    if lease.notice_given_on:
        rows.append((_("Notice given"), _day(lease.notice_given_on)))
    rows.append((_("Move-out expected by") if data["expected"] else _("Moved out"), moved))
    story.append(_facts(rows))

    story.append(_p(_("Rent account"), SECTION))
    story.append(_amounts([(r["label"], money(r["amount"])) for r in data["account"]],
                          total=(_("Balance owed") if data["balance"] >= 0 else _("In credit"),
                                 money(abs(data["balance"])))))
    if data["unpaid"]:
        story += [Spacer(1, 2 * mm), _p(_("Still unpaid"), LABEL)]
        story.append(_amounts([(
            (f"{d.invoice.number} · " if d.invoice else "") + _("due %(day)s") % {"day": _day(d.due)},
            money(d.amount)) for d in data["unpaid"]]))

    story.append(_p(_("Deposits"), SECTION))
    if not data["deposits"]:
        story.append(_p(_("No deposit was recorded for this lease.")))
    for t in data["deposits"]:
        rows = [(_("Received"), money(t["received"]))]
        for e in t["entries"]:
            if e.kind == e.Kind.DEDUCTION:
                label = _("Deducted %(day)s: %(reason)s") % {"day": _day(e.entry_date), "reason": e.reason}
                rows.append((label, money(e.amount)))
        if t["refunded"]:
            rows.append((_("Refunded"), money(-t["refunded"])))
        if t["transferred"]:
            rows.append((_("Transferred"), money(t["transferred"])))
        story += [_p(t["label"], LABEL), _amounts(rows, total=(_("Still held"), money(t["held"]))),
                  Spacer(1, 2 * mm)]

    story.append(_p(_("Condition"), SECTION))
    c = data["condition"]
    if c is None:
        story.append(_p(_("No move-out inspection was recorded.")))
    elif c["compared"]:
        story.append(_p(_("Inspected on %(day)s: %(worse)s of %(items)s items worse than at move-in.") % {
            "day": _day(c["report"].inspected_on), "worse": c["worse"], "items": c["items"]}))
    else:
        story.append(_p(_("Inspected on %(day)s: %(items)s items; there was no move-in report to compare.") % {
            "day": _day(c["report"].inspected_on), "items": c["items"]}))

    story.append(_p(_("Settlement"), SECTION))
    balance = data["balance"]
    rows = [(_("Deposit still held"), money(data["held"])),
            (_("Less balance owed") if balance >= 0 else _("Plus credit on the account"), money(abs(balance)))]
    story.append(_amounts(rows, total=(settlement_text(data["due"]), money(abs(data["due"])))))

    story += [Spacer(1, 3 * mm), _p(_(
        "Figures are from the rent and deposit records on the date above. Payments or deductions "
        "recorded later change them."), SMALL)]
    story.append(_signature_block(data, _("Agreed by"), [(_("For the landlord"), data["organization"])]
                                  + [(_("Tenant"), t.name) for t in data["tenants"]]))
    _footer(story, lease.organization)
    doc.build(story)
    return buf.getvalue()
