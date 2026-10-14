"""The invoice as an A4 PDF (D-076), drawn when asked for, in the style of the lease paperwork.

It is attached to the invoice email and downloaded from the invoice page and the tenant portal.
Nothing is stored: the PDF shows the invoice as it stands, with what is paid on it so far.
"""

import io

from django.utils import timezone
from django.utils.translation import gettext as _
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, Spacer, Table, TableStyle

from core.money import format_money
from leases.pdf import BODY, HEAD, LABEL, LINE, RIGHT, RIGHT_BOLD, SMALL, _day, _doc, _footer, _p

from .models import Invoice

ACCENT = colors.HexColor("#1D4E89")
TINT = colors.HexColor("#F1F4F8")
_CAPTION = ParagraphStyle("inv_caption", parent=SMALL, fontName="Helvetica-Bold", fontSize=7.5,
                          textColor=colors.HexColor("#6B7280"))
_CAPTION_RIGHT = ParagraphStyle("inv_caption_right", parent=_CAPTION, alignment=2)
_TITLE_RIGHT = ParagraphStyle("inv_title", parent=BODY, fontName="Helvetica-Bold", fontSize=18, leading=22,
                              alignment=2, textColor=ACCENT)
_NUMBER_RIGHT = ParagraphStyle("inv_number", parent=RIGHT, textColor=colors.HexColor("#4B5563"))
_DUE_LABEL = ParagraphStyle("inv_due_label", parent=BODY, fontName="Helvetica-Bold", fontSize=9,
                            alignment=2, textColor=colors.white)
_DUE_AMOUNT = ParagraphStyle("inv_due_amount", parent=_DUE_LABEL, fontSize=11)


def _lines(text):
    """Owner-typed text with its line breaks kept, safe inside a Paragraph."""
    return "<br/>".join(escape(line) for line in text.splitlines())


def _accent(org):
    try:
        return colors.HexColor(org.brand_color) if org.brand_color else ACCENT
    except ValueError:
        return ACCENT


def _picture(field, high):
    """An uploaded picture scaled to `high` tall (at most 50 mm wide), or None if there is none or it is unreadable."""
    if not field:
        return None
    try:
        width, height = ImageReader(field.path).getSize()
        wide = min(high * width / height, 50 * mm)
        return Image(field.path, width=wide, height=wide * height / width)
    except Exception:  # missing file or an unreadable picture: print the invoice without it
        return None


def _logo(org):
    return _picture(org.logo, 16 * mm)


def _earlier_unpaid(invoice):
    """Other invoices on this lease still owing, oldest first, as (invoice, outstanding)."""
    older = (Invoice.objects.filter(lease=invoice.lease, status__in=Invoice.OPEN)
             .exclude(pk=invoice.pk).filter(due_date__lte=invoice.due_date).order_by("due_date", "pk"))
    return [(i, i.outstanding) for i in older if i.outstanding > 0]


def render_invoice(invoice: Invoice) -> bytes:
    lease = invoice.lease
    unit = lease.unit
    org = invoice.organization
    accent = _accent(org)
    title_right = ParagraphStyle("inv_title_org", parent=_TITLE_RIGHT, textColor=accent)

    def money(value):
        return format_money(value, invoice.currency)

    lease_tenants = [lt.tenant for lt in lease.lease_tenants.select_related("tenant").order_by("pk")]
    tenants = [t.name for t in lease_tenants]
    lines = [line for line in invoice.lines.select_related("charge_type").order_by("pk") if not line.is_void]

    void = invoice.status == Invoice.Status.VOID

    buf = io.BytesIO()
    doc = _doc(buf, f"{_('Invoice')} {invoice.number}")

    # Header: who is billing on the left, what this is on the right.
    left = []
    if (logo := _logo(org)) is not None:
        logo.hAlign = "LEFT"
        left += [logo, Spacer(1, 2 * mm)]
    left.append(_p(org.display_name, HEAD))
    for text in (org.address, " · ".join(x for x in (org.billing_phone, org.billing_email) if x),
                 _("KRA PIN %(pin)s") % {"pin": org.kra_pin} if org.kra_pin else ""):
        if text:
            left.append(_p(text, SMALL))
    right = [Paragraph(_("INVOICE") if not void else _("INVOICE – VOID"), title_right),
             Paragraph(escape(invoice.number or _("Draft")), _NUMBER_RIGHT)]
    header = Table([[left, right]], colWidths=[None, 70 * mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, accent), ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
    ]))
    story = [header, Spacer(1, 7 * mm)]

    # Billed to, and the dates that matter.
    billed = [Paragraph(_("BILLED TO"), _CAPTION), _p(", ".join(tenants), LABEL),
              _p(f"{unit.property.name}, {unit.code}"), _p(f"{org.vocab['lease']} {lease.number}", SMALL)]
    if org.invoice_show_tenant_contact:
        for t in lease_tenants:
            contact = " · ".join(x for x in (t.phone, t.email) if x)
            if contact:
                billed.append(_p(contact, SMALL))
    dates = Table([
        [Paragraph(_("Issued"), _CAPTION), _p(_day(invoice.issue_date), RIGHT)],
        [Paragraph(_("Period"), _CAPTION), _p(invoice.period_label, RIGHT)],
        [Paragraph(_("Due on"), _CAPTION), _p(_day(invoice.due_date), RIGHT_BOLD)],
    ], colWidths=[22 * mm, 48 * mm])
    dates.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    meta = Table([[billed, dates]], colWidths=[None, 70 * mm])
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [meta, Spacer(1, 8 * mm)]

    # The lines.
    rows = [[Paragraph(_("DESCRIPTION"), _CAPTION), Paragraph(_("AMOUNT"), _CAPTION_RIGHT)]]
    for line in lines:
        rows.append([_p(line.description), _p(money(line.amount), RIGHT)])
        if line.tax_amount:
            rows.append([_p(_("VAT on %(what)s") % {"what": line.description}, SMALL),
                         _p(money(line.tax_amount), RIGHT)])
    table = Table(rows, colWidths=[None, 45 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, 0), TINT),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [table, Spacer(1, 4 * mm)]

    # Totals, right-aligned, with the amount due set apart.
    totals = [(_("Subtotal"), money(invoice.subtotal))]
    if invoice.tax_total:
        totals.append((_("VAT"), money(invoice.tax_total)))
    totals += [(_("Total"), money(invoice.total)), (_("Paid so far"), money(invoice.amount_paid))]
    rows = [[_p(label, RIGHT), _p(amount, RIGHT)] for label, amount in totals]
    rows.append([Paragraph(_("AMOUNT DUE"), _DUE_LABEL), Paragraph(escape(money(invoice.outstanding)), _DUE_AMOUNT)])
    sums = Table(rows, colWidths=[35 * mm, 45 * mm], hAlign="RIGHT")
    sums.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("BACKGROUND", (0, -1), (-1, -1), accent),
        ("TOPPADDING", (0, -1), (-1, -1), 6), ("BOTTOMPADDING", (0, -1), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(sums)

    # Earlier invoices still owing, so the tenant sees what to pay in all.
    earlier = _earlier_unpaid(invoice) if org.invoice_show_previous_balance and not void else []
    if earlier:
        rows = [[Paragraph(_("EARLIER UNPAID INVOICES"), _CAPTION), Paragraph(_("DUE"), _CAPTION),
                 Paragraph(_("OWING"), _CAPTION_RIGHT)]]
        for old, owing in earlier:
            rows.append([_p(f"{old.number} · {old.period_label}", SMALL), _p(_day(old.due_date), SMALL),
                         _p(money(owing), RIGHT)])
        total_owing = invoice.outstanding + sum(owing for _i, owing in earlier)
        rows.append([_p(_("Total owing now, with this invoice"), LABEL), "", _p(money(total_owing), RIGHT_BOLD)])
        prev = Table(rows, colWidths=[None, 28 * mm, 40 * mm])
        prev.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BACKGROUND", (0, 0), (-1, 0), TINT),
            ("LINEBELOW", (0, 1), (-1, -2), 0.4, LINE), ("LINEABOVE", (0, -1), (-1, -1), 0.8, accent),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story += [Spacer(1, 6 * mm), prev]

    # How to pay.
    if not void and invoice.outstanding > 0:
        how = [Paragraph(_("HOW TO PAY"), _CAPTION)]
        if org.invoice_payment_info:
            how.append(Paragraph(_lines(org.invoice_payment_info), BODY))
        how.append(_p(_("Use account number %(ref)s so the payment is matched to this invoice.")
                      % {"ref": unit.payment_reference}))
        pay = Table([[how]], colWidths=[None])
        pay.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("BACKGROUND", (0, 0), (-1, -1), TINT),
                                 ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
        story += [Spacer(1, 8 * mm), pay]
    if invoice.notes:
        story += [Spacer(1, 4 * mm), _p(invoice.notes, SMALL)]
    if org.invoice_terms:
        story += [Spacer(1, 5 * mm), Paragraph(_("TERMS"), _CAPTION),
                  Paragraph(_lines(org.invoice_terms), SMALL)]
    if (sign := _picture(org.stamp, 18 * mm)) is not None:
        sign.hAlign = "LEFT"
        story += [Spacer(1, 6 * mm), sign]
        story += [_p(_("Authorised signature, %(who)s") % {"who": org.display_name}, SMALL)]
    story += [Spacer(1, 6 * mm),
              _p(_("Printed %(when)s.") % {"when": timezone.localtime().strftime("%d %b %Y %H:%M")}, SMALL)]
    _footer(story, org)
    doc.build(story)
    return buf.getvalue()

