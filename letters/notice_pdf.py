"""Notices to vacate and demand letters as A4 PDFs (D-109), drawn once at issue from the frozen facts.

The wording comes from notices.paragraphs and notices.arrears_rows, so the preview, the PDF and the check page agree.
"""

import io
from xml.sax.saxutils import escape

from django.utils import timezone
from django.utils.translation import gettext as _
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import NoticeLetter
from .notices import arrears_rows, paragraphs, premises, title
from .pdf import BODY, HEAD, LABEL, SMALL, TITLE
from .services import verify_url


def _p(text, style=BODY):
    return Paragraph(escape(str(text)), style)


def _arrears_table(data: dict) -> Table:
    head = [_p(_("Invoice"), LABEL), _p(_("Period"), LABEL), _p(_("Due"), LABEL), _p(_("Unpaid"), LABEL)]
    rows = arrears_rows(data)
    body = [[_p(c, LABEL if i == len(rows) - 1 else BODY) for c in row] for i, row in enumerate(rows)]
    table = Table([head, *body], colWidths=[None, 38 * mm, 28 * mm, 34 * mm])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


def render_pdf(letter: NoticeLetter) -> bytes:
    data = letter.facts
    org = letter.organization
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm,
                            bottomMargin=18 * mm, title=f"{letter.get_kind_display()} {letter.number}")
    issued = timezone.localtime(letter.issued_at)
    issuer = letter.issued_by.get_full_name() or letter.issued_by.get_username()
    contact = " · ".join(c for c in (data["org_phone"], data["org_email"]) if c)

    story = [_p(data["organization"], HEAD)]
    if contact:
        story += [Spacer(1, 1 * mm), _p(contact, SMALL)]
    story += [
        Spacer(1, 2 * mm),
        _p(f"{letter.number} · {issued:%d %b %Y}", SMALL),
        Spacer(1, 6 * mm),
        _p(_("To: %(tenants)s") % {"tenants": ", ".join(data["tenants"]) or "—"}, LABEL),
        _p(premises(data)),
        Spacer(1, 6 * mm),
        _p(title(letter.kind), TITLE),
        Spacer(1, 4 * mm),
    ]
    body = paragraphs(letter.kind, data)
    for i, text in enumerate(body):
        story += [_p(text), Spacer(1, 3 * mm)]
        if letter.kind == NoticeLetter.Kind.DEMAND and i == 0:
            story += [_arrears_table(data), Spacer(1, 4 * mm)]
    story += [
        Spacer(1, 8 * mm),
        _p(issuer, LABEL),
        _p(_("for %(organization)s") % {"organization": data["organization"]}),
        Spacer(1, 4 * mm),
        _p(_("Signature: ______________________________")),
        Spacer(1, 10 * mm),
        _p(_("ACKNOWLEDGEMENT OF RECEIPT"), LABEL),
        Spacer(1, 2 * mm),
        _p(_("Received by (name): ______________________________")),
        Spacer(1, 3 * mm),
        _p(_("Signature: ____________________    Date: ______________")),
        Spacer(1, 10 * mm),
        _p(_("To check this letter is genuine and unchanged, open %(url)s") % {"url": verify_url(letter)}, SMALL),
    ]
    if org.document_footer:
        story += [Spacer(1, 2 * mm), _p(org.document_footer, SMALL)]
    doc.build(story)
    return buf.getvalue()
