"""Receipts (doc 11 §9): numbered RCT-YYYY-NNNNNN and rendered to PDF with ReportLab (D-043).

The PDF is drawn once, at confirmation, and stored; it records the balance as it stood then.
It is served only through a permission-checked view, and its file name is a random UUID.
"""

import io
import secrets
import uuid

from django.core.files.base import ContentFile
from django.utils import timezone
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from billing.invoicing import lease_balance
from core.money import format_money
from core.numbering import next_number

from .models import Payment, Receipt


def issue_receipt(payment: Payment) -> Receipt:
    """Numbers and renders the receipt. Runs inside the confirming transaction."""
    now = timezone.now()
    receipt = Receipt(organization=payment.organization, payment=payment, issued_at=now,
                      number=next_number(payment.organization, "receipt", prefix="RCT",
                                         period=str(timezone.localdate().year)),
                      share_token=secrets.token_urlsafe(16))
    receipt.pdf.save(f"{uuid.uuid4().hex}.pdf", ContentFile(render_pdf(receipt)), save=False)
    receipt.save()
    return receipt


def receipt_data(receipt: Receipt) -> dict:
    payment = receipt.payment
    lease = payment.lease
    unit = lease.unit
    currency = lease.currency
    balance = lease_balance(lease)
    return {
        "organization": payment.organization.name,
        "number": receipt.number,
        "issued": timezone.localtime(receipt.issued_at).strftime("%d %b %Y %H:%M"),
        "tenant": payment.tenant.name if payment.tenant else "",
        "property": unit.property.name,
        "unit": unit.code,
        "reference": unit.payment_reference,
        "lease": lease.number,
        "amount": format_money(payment.amount, currency),
        "method": payment.get_method_display(),
        "payment_reference": payment.reference,
        "paid_at": payment.paid_at.strftime("%d %b %Y"),
        "allocations": [(a.invoice.number, format_money(a.amount, currency))
                        for a in payment.allocations.select_related("invoice").order_by("pk")],
        "unallocated": format_money(payment.unallocated, currency) if payment.unallocated > 0 else "",
        "balance": format_money(abs(balance), currency),
        "balance_label": "In credit" if balance < 0 else "Balance due",
    }


def render_pdf(receipt: Receipt) -> bytes:
    d = receipt_data(receipt)
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A5)
    pdf.setTitle(f"Receipt {d['number']}")
    width, height = A5
    left, right = 14 * mm, width - 14 * mm
    y = height - 18 * mm

    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(left, y, d["organization"][:48])
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawRightString(right, y, "RECEIPT")
    y -= 6 * mm
    pdf.setFont("Helvetica", 9)
    pdf.drawRightString(right, y, d["number"])
    y -= 4.5 * mm
    pdf.drawRightString(right, y, f"Issued {d['issued']}")
    y -= 6 * mm
    pdf.line(left, y, right, y)
    y -= 8 * mm

    def row(label, value, bold=False):
        nonlocal y
        pdf.setFont("Helvetica", 9)
        pdf.drawString(left, y, label)
        pdf.setFont("Helvetica-Bold" if bold else "Helvetica", 10 if bold else 9)
        pdf.drawRightString(right, y, str(value)[:60])
        y -= 6 * mm

    row("Received from", d["tenant"])
    row("Property / unit", f"{d['property']} · {d['unit']}")
    row("Account reference", d["reference"])
    row("Lease", d["lease"])
    row("Date paid", d["paid_at"])
    row("Method", d["method"])
    if d["payment_reference"]:
        row("Reference", d["payment_reference"])
    y -= 2 * mm
    row("Amount received", d["amount"], bold=True)

    if d["allocations"] or d["unallocated"]:
        y -= 3 * mm
        pdf.line(left, y + 4 * mm, right, y + 4 * mm)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(left, y, "Applied to")
        y -= 6 * mm
        for number, amount in d["allocations"]:
            row(f"Invoice {number}", amount)
        if d["unallocated"]:
            row("Held as credit", d["unallocated"])

    y -= 3 * mm
    pdf.line(left, y + 4 * mm, right, y + 4 * mm)
    row(d["balance_label"] + " after this payment", d["balance"], bold=True)

    pdf.setFont("Helvetica-Oblique", 7)
    pdf.drawString(left, 12 * mm, "Keep this receipt as proof of payment.")
    pdf.showPage()
    pdf.save()
    return buf.getvalue()
