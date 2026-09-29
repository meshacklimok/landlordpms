"""The rental income pack (D-050) and the owner statement (D-053) as A4 PDFs, drawn with ReportLab.

Drawn on request from the live figures; nothing is stored.
"""

import io
from xml.sax.saxutils import escape

from django.utils import timezone
from django.utils.translation import gettext as _
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.money import format_money

from .income import Pack
from .statements import Statement

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=9, leading=12)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
HEAD = ParagraphStyle("head", parent=BODY, fontName="Helvetica-Bold", fontSize=15, leading=19)
TITLE = ParagraphStyle("title", parent=BODY, fontName="Helvetica-Bold", fontSize=11, leading=15, keepWithNext=1)
GRID = TableStyle([
    ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
    ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
    ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#DDDDDD")),
    ("TOPPADDING", (0, 0), (-1, -1), 2),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
])


def _p(text, style=BODY):
    return Paragraph(escape(str(text)), style)


def _n(value) -> str:
    return "" if value is None else f"{value:,.2f}"


def _table(rows, total=True, first_width=None):
    table = Table(rows, colWidths=[first_width] + [None] * (len(rows[0]) - 1), repeatRows=1,
                  hAlign="LEFT")
    style = TableStyle(GRID.getCommands())
    if total:
        style.add("FONT", (0, -1), (-1, -1), "Helvetica-Bold", 8)
        style.add("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.black)
    table.setStyle(style)
    return table


def render_pack(pack: Pack) -> bytes:
    org = pack.organization
    c = pack.currency
    t = pack.total
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title=_("Rental income %(year)s") % {"year": pack.year})
    money_head = [_("Rent billed"), _("Other billed"), _("Received"), _("Rent received"), _("Other received"),
                  _("Deposits"), _("Not applied"), _("Taxable rent")]

    def money(row):
        return [_n(row.rent_billed), _n(row.other_billed), _n(row.received), _n(row.rent_received),
                _n(row.other_received), _n(row.deposit_received), _n(row.unapplied), _n(row.taxable)]

    story = [
        _p(org.display_name, HEAD),
        _p(_("Rental income pack, %(year)s. Drawn %(day)s.") % {
            "year": pack.year, "day": f"{timezone.localdate():%d %b %Y}"}, SMALL),
        Spacer(1, 5 * mm),
        _table([
            [_("Summary"), ""],
            [_("Tax residence"), pack.residence_label],
            [_("Rent received, with money not yet applied"), format_money(t.taxable, c)],
            [_("Rate"), ", ".join(f"{span} {rate}" for span, rate in pack.rates)],
            [_("Tax estimate"), format_money(t.tax, c) if t.tax is not None else "—"],
        ], total=False, first_width=80 * mm),
        Spacer(1, 6 * mm),
        _p(_("By month"), TITLE),
        _table([[_("Month"), *money_head, _("Rate"), _("Tax estimate")]]
               + [[row.label, *money(row), row.rate_label, _n(row.tax)] for row in pack.months]
               + [[t.label, *money(t), "", _n(t.tax)]]),
        Spacer(1, 6 * mm),
        _p(_("By property"), TITLE),
        _table([[_("Property"), *money_head]] + [[row.label, *money(row)] for row in pack.properties]
               + [[t.label, *money(t)]], first_width=60 * mm),
        Spacer(1, 6 * mm),
        _p(_("Payments received"), TITLE),
    ]
    if pack.receipts:
        rows = [[_("Date"), _("Receipt"), _("Tenant"), _("Property · unit"), _("Amount"), _("Rent"), _("Other"),
                 _("Deposit"), _("Not applied")]]
        for part in pack.receipts:
            p = part.payment
            receipt = getattr(p, "receipt", None)
            rows.append([f"{p.paid_at:%d %b}", receipt.number if receipt else "", p.tenant.name if p.tenant else "",
                         f"{p.lease.unit.property.name} · {p.lease.unit.code}", _n(p.amount), _n(part.rent),
                         _n(part.other), _n(part.deposit), _n(part.unapplied)])
        table = _table(rows, total=False)
        table.setStyle(TableStyle([("ALIGN", (1, 0), (3, -1), "LEFT")]))
        story.append(table)
    else:
        story.append(_p(_("No payments in this year.")))
    story += [Spacer(1, 6 * mm), _p(_("Notes"), TITLE)] + [_p(f"• {note}") for note in pack.notes]
    if org.document_footer:
        story += [Spacer(1, 4 * mm), _p(org.document_footer, SMALL)]
    doc.build(story)
    return buf.getvalue()


def render_statement(st: Statement) -> bytes:
    """The monthly owner statement (D-053), portrait A4."""
    org = st.organization
    c = st.currency
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm,
                            bottomMargin=14 * mm, title=_("Owner statement %(month)s") % {"month": st.label})
    summary = [
        [_("Summary"), ""],
        [_("Rent collected"), format_money(st.rent, c)],
        [_("Other charges collected"), format_money(st.other, c)],
        [_("Total collected"), format_money(st.collected, c)],
    ]
    if st.has_fee:
        summary.append([_("Less management fee"), format_money(-st.fee, c)])
    summary += [
        [_("Less expenses"), _("Not tracked yet")],
        [_("Due to the owner"), format_money(st.due, c)],
    ]
    summary_table = _table(summary, first_width=80 * mm)
    paid = []
    if st.shows_remittances:
        paid = [["", ""], [_("Paid to the owner"), format_money(st.remitted, c)]]
        paid += [["  " + _("%(day)s, %(method)s %(ref)s") % {
            "day": f"{r.paid_on:%d %b %Y}", "method": r.get_method_display(), "ref": r.reference},
            format_money(r.amount, c)] for r in st.remittances]
        paid.append([_("Still to pay"), format_money(st.remaining, c)])
    story = [
        _p(org.display_name, HEAD),
        _p(_("Owner statement for %(month)s") % {"month": st.label}, TITLE),
        _p(_("To: %(owner)s. Drawn %(day)s.") % {"owner": st.recipient, "day": f"{timezone.localdate():%d %b %Y}"},
           SMALL),
        Spacer(1, 5 * mm),
        summary_table,
    ]
    extra = [[_("Rent and charges billed for the month"), format_money(st.billed, c)],
             [_("Owed by tenants at month end"), format_money(st.balance, c)]]
    if st.deposit:
        extra.insert(0, [_("Deposits received (held, not included)"), format_money(st.deposit, c)])
    if paid:
        story += [Spacer(1, 3 * mm), _table(paid, first_width=80 * mm)]
    story += [Spacer(1, 3 * mm), _table([["", ""], *extra], total=False, first_width=80 * mm)]
    head = [_("Unit"), _("Tenant"), _("Billed"), _("Collected"), _("Deposit"), _("Owed at month end")]
    for block in st.blocks:
        title = block.property.name
        if block.rentable:
            title += " · " + _("%(o)s of %(r)s units occupied") % {"o": block.occupied, "r": block.rentable}
        if block.fee_percent:
            title += " · " + _("fee %(pct)s%% of rent") % {"pct": f"{block.fee_percent:g}"}
        rows = [head] + [[line.lease.unit.code, line.tenant, _n(line.billed), _n(line.collected), _n(line.deposit),
                          _n(line.balance)] for line in block.lines]
        rows.append([_("Total"), "", _n(block.billed), _n(block.collected), _n(block.deposit), _n(block.balance)])
        table = _table(rows, first_width=22 * mm)
        table.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "LEFT")]))
        story += [Spacer(1, 6 * mm), _p(title, TITLE), table]
        if block.fee_percent:
            story.append(_p(_("Management fee: %(fee)s on rent collected of %(rent)s.") % {
                "fee": format_money(block.fee, c), "rent": format_money(block.rent, c)}, SMALL))
    story += [Spacer(1, 6 * mm), _p(_("Notes"), TITLE)] + [_p(f"• {note}", SMALL) for note in st.notes]
    if org.document_footer:
        story += [Spacer(1, 4 * mm), _p(org.document_footer, SMALL)]
    doc.build(story)
    return buf.getvalue()
