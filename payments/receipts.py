"""Receipts (doc 11 §9): numbered RCT-YYYY-NNNNNN and rendered to PDF with ReportLab (D-043).

The PDF is drawn once, at confirmation, and stored; it records the balance as it stood then.
It is served only through a permission-checked view, and its file name is a random UUID.
"""

import secrets
import uuid

from django.core.files.base import ContentFile
from django.utils import timezone
from django.utils.translation import gettext as _
from reportlab.lib.pagesizes import A5
from reportlab.lib.units import mm

from billing.invoicing import lease_balance
from core import brand
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
        # The organization's words for the property and unit, and for the lease (D-130): "Property / unit" and
        # "Lease" for landlords.
        "place_label": f"{payment.organization.vocab['property']} / {payment.organization.vocab['unit'].lower()}",
        "lease_label": str(payment.organization.vocab["lease"]),
        "amount": format_money(payment.amount, currency),
        "method": payment.get_method_display(),
        "payment_reference": payment.reference,
        "paid_at": payment.paid_at.strftime("%d %b %Y"),
        "allocations": [(a.invoice.number, format_money(a.amount, currency))
                        for a in payment.allocations.select_related("invoice").order_by("pk")],
        "unallocated": format_money(payment.unallocated, currency) if payment.unallocated > 0 else "",
        "balance": format_money(abs(balance), currency),
        "balance_label": _("In credit") if balance < 0 else _("Balance due"),
    }


def render_pdf(receipt: Receipt) -> bytes:
    d = receipt_data(receipt)
    org = receipt.payment.organization
    stub = brand.Stub(A5, f"{_('Receipt')} {d['number']}", org.display_name, _("RECEIPT"), d["number"],
                      _("Issued %(when)s") % {"when": d["issued"]}, org=org, lines=brand.org_lines(org),
                      footer_label=f"{org.display_name} · {_('Receipt')} {d['number']}")
    row = stub.row
    row(_("Received from"), d["tenant"])
    row(d["place_label"], f"{d['property']} · {d['unit']}")
    row(_("Account reference"), d["reference"])
    row(d["lease_label"], d["lease"])
    row(_("Date paid"), d["paid_at"])
    row(_("Method"), d["method"])
    if d["payment_reference"]:
        row(_("Reference"), d["payment_reference"])
    stub.y -= 2 * mm
    stub.total(_("Amount received"), d["amount"])

    if d["allocations"] or d["unallocated"]:
        stub.y -= 1 * mm
        stub.heading(_("Applied to"))
        for number, amount in d["allocations"]:
            row(_("Invoice %(number)s") % {"number": number}, amount)
        if d["unallocated"]:
            row(_("Held as credit"), d["unallocated"])

    stub.rule()
    row(_("%(what)s after this payment") % {"what": d["balance_label"]}, d["balance"], bold=True)
    stub.y -= 1 * mm
    stub.note(_("Keep this receipt as proof of payment."))
    return stub.finish()
