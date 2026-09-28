"""What fires a notification (doc 11 §27, D-044 step 2).

Billing and payments call these at the moment of the event; `send_reminders` runs in the
daily job for reminders tied to a date. Each call writes one `Message` per recipient with a
dedupe key, so running any of them twice sends nothing twice.
"""

import datetime

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organization
from billing.invoicing import lease_balance
from billing.models import Invoice
from core.money import ZERO, format_money
from leases.models import Lease

from . import catalog
from .delivery import effective_rule, notify, staff_with

# A daily reminder that was missed (the job did not run) is still sent this many days late, no later.
CATCH_UP_DAYS = 2


# ---------------------------------------------------------------------------
# Recipients and fields
# ---------------------------------------------------------------------------


def lease_recipients(lease: Lease, *, include_co_tenants: bool, first=None) -> list:
    """The primary tenant (or `first`, such as the payer), then co-tenants if the rule asks for them."""
    tenants = [lt.tenant for lt in lease.lease_tenants.select_related("tenant").all()]
    lead = first or lease.primary_tenant or (tenants[0] if tenants else None)
    if lead is None:
        return []
    others = [t for t in tenants if t.pk != lead.pk] if include_co_tenants else []
    return [lead, *others]


def _date(day: datetime.date) -> str:
    # Numeric, so it reads the same in English and Swahili.
    return day.strftime("%d/%m/%Y")


def _balance(lease: Lease) -> str:
    # A tenant in credit owes nothing; the SMS does not try to explain credit.
    return format_money(max(lease_balance(lease), ZERO), lease.currency)


def _tenancy(lease: Lease, tenant) -> dict:
    unit = lease.unit
    return {"tenant_name": tenant.name, "unit": unit.code, "property": unit.property.name,
            "pay_reference": unit.payment_reference}


def _lease(lease: Lease) -> Lease:
    return (Lease.all_objects.select_related("organization", "unit__property")
            .prefetch_related("lease_tenants__tenant").get(pk=lease.pk))


def receipt_link(receipt) -> str:
    if not receipt.share_token:
        return ""
    return settings.SITE_URL + reverse("receipt_link", args=[receipt.share_token])


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def invoice_issued(invoice: Invoice, *, send_now: bool = False) -> int:
    """A new invoice from the monthly run. Not called when a month is billed again after a correction."""
    if invoice.total <= 0:
        return 0
    lease = _lease(invoice.lease)
    org = lease.organization
    ntype = catalog.get("invoice_issued")
    rule = effective_rule(org, ntype)
    fields = {"invoice_number": invoice.number, "amount": format_money(invoice.total, invoice.currency),
              "due_date": _date(invoice.due_date), "balance": _balance(lease)}
    sent = 0
    for tenant in lease_recipients(lease, include_co_tenants=rule.include_co_tenants):
        if notify(org, ntype.codename, tenant=tenant, context={**_tenancy(lease, tenant), **fields},
                  dedupe_key=f"{ntype.codename}:{invoice.pk}:{tenant.pk}", lease=lease, invoice=invoice,
                  send_now=send_now):
            sent += 1
    return sent


def payment_received(payment) -> int:
    """A confirmed payment, to the payer first, with a link to the receipt. Runs in the confirming transaction."""
    lease = _lease(payment.lease)
    org = lease.organization
    ntype = catalog.get("payment_received")
    rule = effective_rule(org, ntype)
    receipt = getattr(payment, "receipt", None)
    fields = {"amount": format_money(payment.amount, lease.currency), "paid_on": _date(payment.paid_at),
              "receipt_number": receipt.number if receipt else "", "balance": _balance(lease),
              "receipt_link": receipt_link(receipt) if receipt else ""}
    sent = 0
    for tenant in lease_recipients(lease, include_co_tenants=rule.include_co_tenants, first=payment.tenant):
        if notify(org, ntype.codename, tenant=tenant, context={**_tenancy(lease, tenant), **fields},
                  dedupe_key=f"{ntype.codename}:{payment.pk}:{tenant.pk}", lease=lease, payment=payment,
                  created_by=payment.confirmed_by):
            sent += 1
    return sent


def payment_pending_review(payment) -> int:
    """In-app, to everyone who may confirm payments for the property."""
    lease = _lease(payment.lease)
    org = lease.organization
    recorder = payment.recorded_by
    context = {"tenant_name": payment.tenant.name if payment.tenant else "", "unit": lease.unit.code,
               "amount": format_money(payment.amount, lease.currency),
               "recorded_by": str(recorder) if recorder else ""}
    sent = 0
    for user in staff_with(org, "payments.confirm", lease.unit.property):
        if notify(org, "payment_pending_review", user=user, context=context,
                  dedupe_key=f"payment_pending_review:{payment.pk}:{user.pk}", lease=lease, payment=payment,
                  created_by=recorder):
            sent += 1
    return sent


# ---------------------------------------------------------------------------
# Daily reminders
# ---------------------------------------------------------------------------


def _open_invoices(org: Organization):
    return (Invoice.objects.filter(organization=org, status__in=Invoice.OPEN)
            .select_related("lease").order_by("due_date", "pk"))


def due_soon_offset(invoice: Invoice, offsets, today: datetime.date) -> int | None:
    """The reminder due today for an invoice not yet due: the nearest offset whose day has come.

    None if no reminder day has come yet, or the invoice was issued on or after it (the invoice
    message already said when it is due).
    """
    reached = [o for o in offsets if o >= 0 and invoice.due_date - datetime.timedelta(days=o) <= today]
    if invoice.due_date < today or not reached:
        return None
    offset = min(reached)
    if invoice.issue_date and invoice.issue_date >= invoice.due_date - datetime.timedelta(days=offset):
        return None
    return offset


def overdue_offset(invoice: Invoice, offsets, today: datetime.date) -> int | None:
    """The overdue reminder due today: offsets count days after the grace period ends (1 = first overdue day).

    A reminder whose day passed more than CATCH_UP_DAYS ago is dropped, not sent late.
    """
    days_overdue = (today - invoice.overdue_after).days
    reached = [o for o in {max(o, 1) for o in offsets} if o <= days_overdue]
    if not reached:
        return None
    offset = max(reached)
    return offset if days_overdue - offset <= CATCH_UP_DAYS else None


def _remind(org, ntype, rule, invoice: Invoice, offset: int) -> int:
    lease = _lease(invoice.lease)
    fields = {"invoice_number": invoice.number, "amount_due": format_money(invoice.outstanding, invoice.currency),
              "due_date": _date(invoice.due_date), "balance": _balance(lease)}
    sent = 0
    for tenant in lease_recipients(lease, include_co_tenants=rule.include_co_tenants):
        if notify(org, ntype.codename, tenant=tenant, context={**_tenancy(lease, tenant), **fields},
                  dedupe_key=f"{ntype.codename}:{invoice.pk}:{offset}:{tenant.pk}", lease=lease, invoice=invoice,
                  send_now=False):
            sent += 1
    return sent


def send_reminders(today: datetime.date | None = None) -> dict[str, int]:
    """Rent due soon and rent overdue, for every active organization. Part of the daily job.

    Messages are queued; `send_due_messages` sends them (after quiet hours if the job runs at night).
    """
    today = today or timezone.localdate()
    counts = {"rent_due_soon": 0, "rent_overdue": 0}
    due_soon, overdue = catalog.get("rent_due_soon"), catalog.get("rent_overdue")
    for org in Organization.objects.filter(archived_at__isnull=True, status=Organization.Status.ACTIVE):
        if not org.is_operational:
            continue
        rule = effective_rule(org, due_soon)
        if rule.enabled and rule.offsets:
            horizon = today + datetime.timedelta(days=max(rule.offsets))
            for invoice in _open_invoices(org).filter(due_date__gte=today, due_date__lte=horizon):
                offset = due_soon_offset(invoice, rule.offsets, today)
                if offset is not None:
                    counts["rent_due_soon"] += _remind(org, due_soon, rule, invoice, offset)
        rule = effective_rule(org, overdue)
        if rule.enabled and rule.offsets:
            earliest = today - datetime.timedelta(days=max(max(rule.offsets), 1) + CATCH_UP_DAYS)
            for invoice in _open_invoices(org).filter(overdue_after__lt=today, overdue_after__gte=earliest):
                if invoice.outstanding <= 0:
                    continue
                offset = overdue_offset(invoice, rule.offsets, today)
                if offset is not None:
                    counts["rent_overdue"] += _remind(org, overdue, rule, invoice, offset)
    return counts
