"""Our invoice and receipt PDFs (D-060 item 8), drawn when asked for; the rows are the record."""

import io

from django.conf import settings
from django.utils import timezone
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from core.money import format_money

from .models import PlatformReceipt, SubscriptionInvoice

SELLER = "LandlordPMS"


def _money(value) -> str:
    return format_money(value, "KES")


def _page(title: str, number: str, dated: str):
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A5)
    pdf.setTitle(f"{title.title()} {number}")
    width, height = A5
    left, right = 14 * mm, width - 14 * mm
    y = height - 18 * mm
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(left, y, SELLER)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawRightString(right, y, title)
    y -= 6 * mm
    pdf.setFont("Helvetica", 9)
    pin = getattr(settings, "PLATFORM_KRA_PIN", "")
    if pin:
        pdf.drawString(left, y, f"KRA PIN {pin}")
    pdf.drawRightString(right, y, number)
    y -= 4.5 * mm
    pdf.drawRightString(right, y, dated)
    y -= 6 * mm
    pdf.line(left, y, right, y)
    return buf, pdf, left, right, y - 8 * mm


class _Rows:
    def __init__(self, pdf, left, right, y):
        self.pdf, self.left, self.right, self.y = pdf, left, right, y

    def __call__(self, label, value, bold=False):
        self.pdf.setFont("Helvetica", 9)
        self.pdf.drawString(self.left, self.y, label)
        self.pdf.setFont("Helvetica-Bold" if bold else "Helvetica", 10 if bold else 9)
        self.pdf.drawRightString(self.right, self.y, str(value)[:60])
        self.y -= 6 * mm

    def line(self):
        self.y -= 2 * mm
        self.pdf.line(self.left, self.y + 4 * mm, self.right, self.y + 4 * mm)


def invoice_pdf(invoice: SubscriptionInvoice) -> bytes:
    buf, pdf, left, right, y = _page("INVOICE", invoice.number, f"Issued {invoice.issued_on:%d %b %Y}")
    row = _Rows(pdf, left, right, y)
    row("Bill to", invoice.customer_name)
    if invoice.customer_kra_pin:
        row("KRA PIN", invoice.customer_kra_pin)
    row("Plan", f"{invoice.plan_name} ({invoice.get_interval_display().lower()})")
    if invoice.period_start:
        row("Period", f"{invoice.period_start:%d %b %Y} to {invoice.period_end:%d %b %Y}")
    else:
        row("Period", "Starts on the day it is paid")
    row("Due", f"{invoice.due_on:%d %b %Y}")
    row.line()
    row("Price", _money(invoice.price))
    if invoice.credit:
        row("Credit for unused days", "-" + _money(invoice.credit))
    if invoice.vat_amount:
        row("Amount before VAT", _money(invoice.net_amount))
        row(f"VAT {invoice.vat_rate:g}%", _money(invoice.vat_amount))
    row("Total", _money(invoice.total), bold=True)
    if invoice.etims_reference:
        row("eTIMS", invoice.etims_reference)
    row.line()
    status = invoice.get_status_display().upper()
    row("Status", status, bold=True)
    if invoice.status == SubscriptionInvoice.Status.OPEN:
        paybill = getattr(settings, "PLATFORM_PAYBILL", "")
        if paybill:
            row("Pay by M-Pesa", f"Paybill {paybill}, account {invoice.number}")
    pdf.setFont("Helvetica-Oblique", 7)
    pdf.drawString(left, 12 * mm, "Prices are before VAT." if not invoice.vat_amount else "")
    pdf.showPage()
    pdf.save()
    return buf.getvalue()


def receipt_pdf(receipt: PlatformReceipt) -> bytes:
    payment = receipt.payment
    issued = timezone.localtime(receipt.issued_at)
    buf, pdf, left, right, y = _page("RECEIPT", receipt.number, f"Issued {issued:%d %b %Y %H:%M}")
    row = _Rows(pdf, left, right, y)
    row("Received from", payment.organization.name)
    row("Date paid", f"{payment.paid_on:%d %b %Y}")
    row("Method", payment.get_method_display())
    row("Reference", payment.reference)
    if payment.invoice_id:
        row("For invoice", payment.invoice.number)
    else:
        row("For", "SMS credit")
    row.line()
    row("Amount received", _money(payment.amount), bold=True)
    pdf.setFont("Helvetica-Oblique", 7)
    pdf.drawString(left, 12 * mm, "Thank you.")
    pdf.showPage()
    pdf.save()
    return buf.getvalue()
