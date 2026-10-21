"""Our invoice and receipt PDFs (D-060 item 8), drawn when asked for; the rows are the record."""

from django.conf import settings
from django.utils import timezone
from reportlab.lib.pagesizes import A5

from core import brand
from core.money import format_money

from . import collect
from .models import PlatformReceipt, SubscriptionInvoice

SELLER = "NyumbaPMS"


def _money(value) -> str:
    return format_money(value, "KES")


def _page(title: str, number: str, dated: str):
    pin = getattr(settings, "PLATFORM_KRA_PIN", "")
    return brand.Stub(A5, f"{title.title()} {number}", SELLER, title, number, dated,
                      lines=[f"KRA PIN {pin}"] if pin else [], footer_label=f"{SELLER} · {title.title()} {number}")


def invoice_pdf(invoice: SubscriptionInvoice) -> bytes:
    stub = _page("INVOICE", invoice.number, f"Issued {invoice.issued_on:%d %b %Y}")
    row = stub.row
    row("Bill to", invoice.customer_name)
    if invoice.customer_kra_pin:
        row("KRA PIN", invoice.customer_kra_pin)
    row("Plan", f"{invoice.plan_name} ({invoice.get_interval_display().lower()})")
    if invoice.period_start:
        row("Period", f"{invoice.period_start:%d %b %Y} to {invoice.period_end:%d %b %Y}")
    else:
        row("Period", "Starts on the day it is paid")
    row("Due", f"{invoice.due_on:%d %b %Y}")
    stub.rule()
    row("Price", _money(invoice.price))
    if invoice.credit:
        row("Credit for unused days", "-" + _money(invoice.credit))
    if invoice.discount:
        row("Discount", "-" + _money(invoice.discount))
    if invoice.vat_amount:
        row("Amount before VAT", _money(invoice.net_amount))
        row(f"VAT {invoice.vat_rate:g}%", _money(invoice.vat_amount))
    stub.total("Total", _money(invoice.total))
    if invoice.etims_reference:
        row("eTIMS", invoice.etims_reference)
    stub.rule()
    row("Status", invoice.get_status_display().upper(), bold=True)
    if invoice.status == SubscriptionInvoice.Status.OPEN:
        paybill = collect.paybill()
        if paybill:
            row("Pay by M-Pesa", f"Paybill {paybill}, account {collect.account_number(invoice)}")
    stub.note("Prices are before VAT." if not invoice.vat_amount else "Thank you for choosing NyumbaPMS.")
    return stub.finish()


def receipt_pdf(receipt: PlatformReceipt) -> bytes:
    payment = receipt.payment
    issued = timezone.localtime(receipt.issued_at)
    stub = _page("RECEIPT", receipt.number, f"Issued {issued:%d %b %Y %H:%M}")
    row = stub.row
    row("Received from", payment.organization.name)
    row("Date paid", f"{payment.paid_on:%d %b %Y}")
    row("Method", payment.get_method_display())
    row("Reference", payment.reference)
    if payment.invoice_id:
        row("For invoice", payment.invoice.number)
    else:
        row("For", "SMS credit")
    stub.rule()
    stub.total("Amount received", _money(payment.amount))
    stub.note("Thank you.")
    return stub.finish()
