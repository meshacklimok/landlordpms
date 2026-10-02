"""The letter as an A4 PDF, drawn with ReportLab like receipts (D-043, D-048).

Drawn once, at issue, from the frozen facts. Everything printed comes from services.statements,
so the check page shows exactly the same wording.
"""

import io
from xml.sax.saxutils import escape

from django.utils import timezone
from django.utils.translation import gettext as _
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import TenancyLetter
from .services import statements, verify_url

BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=14)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=11, textColor=colors.HexColor("#555555"))
LABEL = ParagraphStyle("label", parent=BODY, fontName="Helvetica-Bold")
HEAD = ParagraphStyle("head", parent=BODY, fontName="Helvetica-Bold", fontSize=15, leading=19)
TITLE = ParagraphStyle("title", parent=BODY, fontName="Helvetica-Bold", fontSize=12, leading=16)


def _p(text, style=BODY):
    return Paragraph(escape(str(text)), style)


def render_pdf(letter: TenancyLetter) -> bytes:
    data = letter.facts
    org = letter.organization
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title=f"{_('Tenancy letter')} {letter.number}")
    issued = timezone.localtime(letter.issued_at)
    issuer = letter.issued_by.get_full_name() or letter.issued_by.get_username()

    story = [
        _p(data["organization"], HEAD),
        Spacer(1, 2 * mm),
        _p(f"{letter.number} · {issued:%d %b %Y}", SMALL),
        Spacer(1, 8 * mm),
        _p(_("TENANCY AND PAYMENT RECORD"), TITLE),
        Spacer(1, 4 * mm),
        _p(_("To whom it may concern,")),
        Spacer(1, 3 * mm),
        _p(_("We confirm the following from our records as at %(day)s.") % {"day": f"{issued:%d %b %Y}"}),
        Spacer(1, 5 * mm),
    ]
    rows = [[_p(label, LABEL), [_p(line) for line in lines]] for label, lines in statements(data)]
    table = Table(rows, colWidths=[42 * mm, None])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [
        table,
        Spacer(1, 8 * mm),
        _p(_("This letter states facts from our rent records and is not a guarantee.")),
        Spacer(1, 10 * mm),
        _p(issuer, LABEL),
        _p(_("for %(organization)s") % {"organization": data["organization"]}),
        Spacer(1, 12 * mm),
        _p(_("To check this letter is genuine and unchanged, open %(url)s") % {"url": verify_url(letter)}, SMALL),
    ]
    if org.document_footer:
        story += [Spacer(1, 2 * mm), _p(org.document_footer, SMALL)]
    doc.build(story)
    return buf.getvalue()
