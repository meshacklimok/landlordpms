"""The rental income pack as an A4 PDF, drawn with ReportLab like receipts and letters (D-050).

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

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=9, leading=12)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
HEAD = ParagraphStyle("head", parent=BODY, fontName="Helvetica-Bold", fontSize=15, leading=19)
TITLE = ParagraphStyle("title", parent=BODY, fontName="Helvetica-Bold", fontSize=11, leading=15)
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
    table = Table(rows, colWidths=[first_width] + [None] * (len(rows[0]) - 1), repeatRows=1)
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
