"""What fires a notification (doc 11 §27, D-044 step 2).

Billing and payments call these at the moment of the event; `send_reminders` runs in the
daily job for reminders tied to a date. Each call writes one `Message` per recipient with a
dedupe key, so running any of them twice sends nothing twice.
"""

import datetime
from decimal import Decimal

from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Organization
from billing.invoicing import lease_balance
from billing.models import Invoice
from core.money import ZERO, format_money, round_money
from leases.models import Lease

from . import catalog
from .delivery import effective_rule, notify, staff_with
from .models import Message

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


def message_settings(org):
    from .models import OrganizationMessageSettings

    return OrganizationMessageSettings.objects.filter(organization=org).first()


def signed_balance(lease: Lease):
    """What the tenant owes, negative when in credit. Deposits are never part of the ledger balance
    (doc 14 A1); an organization may count the part of the agreed deposit not yet paid as owed."""
    from billing import deposits

    cfg = message_settings(lease.organization)
    owed = lease_balance(lease)
    if cfg and cfg.balance_includes_deposit:
        owed += max(lease.deposit_amount - deposits.held(lease), ZERO)
    return owed


def balance_amount(lease: Lease):
    """The balance a message shows. Without the credit option, a tenant in credit shows as owing nothing."""
    cfg = message_settings(lease.organization)
    owed = signed_balance(lease)
    return owed if cfg and cfg.balance_shows_credit else max(owed, ZERO)


def _balance(lease: Lease) -> str:
    return format_money(balance_amount(lease), lease.currency)


def _tenancy(lease: Lease, tenant) -> dict:
    unit = lease.unit
    return {"tenant_name": tenant.name, "unit": unit.code, "property": unit.property.name,
            "pay_reference": unit.payment_reference}


def _pay_link(lease: Lease) -> str:
    """The lease's payment link for a rent message, or "" (D-046 item 1)."""
    from mpesa import paylinks

    return paylinks.message_url(lease)


def _lease(lease: Lease) -> Lease:
    return (Lease.all_objects.select_related("organization", "unit__property")
            .prefetch_related("lease_tenants__tenant").get(pk=lease.pk))


def receipt_link(receipt) -> str:
    if not receipt.share_token:
        return ""
    return settings.SITE_URL + reverse("receipt_link", args=[receipt.share_token])


def tenancy_fields(lease: Lease, tenant) -> dict:
    return _tenancy(lease, tenant)


def invoice_fields(invoice: Invoice, lease: Lease) -> dict:
    """The fields of the invoice message (D-044), also used to start a message staff write (D-125)."""
    return {"invoice_number": invoice.number, "amount": format_money(invoice.total, invoice.currency),
            "due_date": _date(invoice.due_date), "balance": _balance(lease), "pay_link": _pay_link(lease)}


def _plain(value) -> str:
    """12,500.00: an amount without the currency, for wording that already says it or leaves it out."""
    return f"{round_money(value):,.2f}"


def payment_breakdown(payment) -> str:
    """What the payment went to, by kind of charge: "Rent: 8,000.00, Water: 500.00".

    A payment settles invoices, not lines, so each invoice's share is spread over its lines in
    proportion to what they cost. Money not yet put against an invoice is shown as "Advance".
    """
    parts: dict[str, Decimal] = {}
    for alloc in payment.allocations.select_related("invoice").prefetch_related("invoice__lines__charge_type"):
        lines = [ln for ln in alloc.invoice.lines.all() if not ln.is_void]
        total = sum((ln.amount + ln.tax_amount for ln in lines), ZERO)
        left = alloc.amount
        for i, ln in enumerate(lines):
            share = left if i == len(lines) - 1 else round_money(alloc.amount * (ln.amount + ln.tax_amount) / total)
            share = min(share, left)
            left -= share
            if share > 0:
                parts[ln.charge_type.name] = parts.get(ln.charge_type.name, ZERO) + share
    advance = payment.amount - payment.allocated
    if advance > 0:
        parts["Advance"] = advance
    return ", ".join(f"{name}: {_plain(amount)}" for name, amount in parts.items())


def payment_fields(payment, lease: Lease) -> dict:
    """The fields of the payment message (D-044), also used to start a message staff write (D-125).

    `amount_plain`, `breakdown` and `balance_signed` let an organization word it its own way, e.g.
    "Thank you for paying {amount_plain} for premise {unit} on {paid_on}. ({breakdown}). Bal c/d: {balance_signed}".
    """
    receipt = getattr(payment, "receipt", None)
    return {"amount": format_money(payment.amount, lease.currency), "paid_on": _date(payment.paid_at),
            "receipt_number": receipt.number if receipt else "", "balance": _balance(lease),
            "receipt_link": receipt_link(receipt) if receipt else "",
            "amount_plain": _plain(payment.amount), "breakdown": payment_breakdown(payment),
            # Owed is positive and credit is negative, as on a statement, with the same deposit rule as {balance}.
            "balance_signed": _plain(signed_balance(lease))}


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
    fields = invoice_fields(invoice, lease)
    sent = 0
    for tenant in lease_recipients(lease, include_co_tenants=rule.include_co_tenants):
        if notify(org, ntype.codename, tenant=tenant, context={**_tenancy(lease, tenant), **fields},
                  dedupe_key=f"{ntype.codename}:{invoice.pk}:{tenant.pk}", lease=lease, invoice=invoice,
                  send_now=send_now):
            sent += 1
    return sent


def invoice_message_previews(lease: Lease, *, total, due_date: datetime.date) -> list[dict]:
    """The invoice message each tenant would get, for an invoice not issued yet. Writes nothing.

    The invoice number is only given at issue, so the text shows a placeholder for it.
    Each row: tenant, channel, text (None when nothing would be sent) and why.
    """
    from .delivery import Skip, preview
    from .rendering import render, template_for

    lease = _lease(lease)
    org = lease.organization
    ntype = catalog.get("invoice_issued")
    rule = effective_rule(org, ntype)
    fields = {"invoice_number": "INV-…", "amount": format_money(total, lease.currency), "due_date": _date(due_date),
              "balance": format_money(max(lease_balance(lease) + total, ZERO), lease.currency),
              "pay_link": _pay_link(lease)}
    rows = []
    for tenant in lease_recipients(lease, include_co_tenants=rule.include_co_tenants):
        chosen, reason = preview(org, ntype.codename, tenant=tenant, rule=rule)
        language = getattr(tenant, "language", "") or catalog.EN
        channel, body = chosen if chosen else (None, None)
        if body is None:  # cannot be sent, but the wording is still shown
            for ch in rule.channels:
                body = template_for(org, ntype, ch, language)
                if body:
                    channel = ch
                    break
        text = render(body, {"org_name": org.display_name, **_tenancy(lease, tenant), **fields}) if body else ""
        rows.append({"tenant": tenant, "channel": channel, "text": text, "sendable": chosen is not None,
                     "reason": Skip(reason).label if reason else ""})
    return rows


def payment_received(payment) -> int:
    """A confirmed payment, to the payer first, with a link to the receipt. Runs in the confirming transaction."""
    lease = _lease(payment.lease)
    org = lease.organization
    ntype = catalog.get("payment_received")
    rule = effective_rule(org, ntype)
    fields = payment_fields(payment, lease)
    sent = 0
    for tenant in lease_recipients(lease, include_co_tenants=rule.include_co_tenants, first=payment.tenant):
        if notify(org, ntype.codename, tenant=tenant, context={**_tenancy(lease, tenant), **fields},
                  dedupe_key=f"{ntype.codename}:{payment.pk}:{tenant.pk}", lease=lease, payment=payment,
                  created_by=payment.confirmed_by):
            sent += 1
    return sent


def message_failed(message: Message) -> int:
    """Tells the staff who send messages that one could not be delivered, once a day per organization."""
    org = message.organization
    day = timezone.localdate()
    sent = 0
    for user in staff_with(org, "messages.send"):
        if notify(org, "messages_failed", user=user, dedupe_key=f"messages_failed:{org.pk}:{user.pk}:{day}"):
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
# M-Pesa (D-045 item 7)
# ---------------------------------------------------------------------------


def mpesa_unmatched(tx) -> int:
    """In-app, to everyone who may match it: the same people who see it in their inbox."""
    from accounts.models import Membership
    from mpesa import inbox

    org = Organization.objects.get(pk=tx.organization_id)
    context = {"amount": format_money(tx.amount, org.currency), "payer": tx.payer_name or tx.payer_phone or "?",
               "reference": tx.bill_ref, "account": str(tx.payment_account), "trans_id": tx.trans_id}
    memberships = (Membership.objects.filter(organization=org, is_active=True, user__is_active=True)
                   .select_related("user", "organization", "role"))
    sent = 0
    for m in memberships:
        if not inbox.visible_transactions(m, "mpesa.match").filter(pk=tx.pk).exists():
            continue
        if notify(org, "mpesa_unmatched", user=m.user, context=context,
                  dedupe_key=f"mpesa_unmatched:{tx.pk}:{m.user.pk}"):
            sent += 1
        notify(org, "mpesa_unmatched_email", user=m.user, context=context,
               dedupe_key=f"mpesa_unmatched_email:{tx.pk}:{m.user.pk}")
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
              "due_date": _date(invoice.due_date), "balance": _balance(lease), "pay_link": _pay_link(lease)}
    sent = 0
    for tenant in lease_recipients(lease, include_co_tenants=rule.include_co_tenants):
        if notify(org, ntype.codename, tenant=tenant, context={**_tenancy(lease, tenant), **fields},
                  dedupe_key=f"{ntype.codename}:{invoice.pk}:{offset}:{tenant.pk}", lease=lease, invoice=invoice,
                  send_now=False):
            sent += 1
    return sent


def notice_given(notice) -> int:
    """A notice to vacate was recorded (D-106): staff who handle move-outs hear of it, and a tenant who gave
    notice hears back with the move-out day. Returns the number of messages written."""
    lease = _lease(notice.lease)
    org, unit = lease.organization, lease.unit
    by = notice.recorded_by
    lead = notice.tenant or lease.primary_tenant
    sent = 0
    context = {"tenant_name": lead.name if lead else "", "property": unit.property.name, "unit": unit.code,
               "given_by": str(notice.get_given_by_display()).lower(), "move_out_on": _date(notice.move_out_on),
               "recorded_by": str(by) if by else ""}
    for user in staff_with(org, "leases.terminate", unit.property):
        if by is not None and user.pk == by.pk:
            continue
        if notify(org, "notice_given", user=user, context=context, lease=lease, created_by=by,
                  dedupe_key=f"notice_given:{notice.pk}:{user.pk}"):
            sent += 1
    if notice.given_by != notice.GivenBy.TENANT:
        return sent
    rule = effective_rule(org, catalog.get("notice_received"))
    fields = {"move_out_on": _date(notice.move_out_on), "balance": _balance(lease)}
    for tenant in lease_recipients(lease, include_co_tenants=rule.include_co_tenants, first=notice.tenant):
        if notify(org, "notice_received", tenant=tenant, context={**_tenancy(lease, tenant), **fields}, lease=lease,
                  created_by=by, dedupe_key=f"notice_received:{notice.pk}:{tenant.pk}"):
            sent += 1
    return sent


def portal_message_received(message) -> int:
    """A tenant wrote to the office in the portal (D-131): staff who can reply hear of it."""
    lease = _lease(message.lease)
    org, unit = lease.organization, lease.unit
    context = {"tenant_name": message.tenant.name, "property": unit.property.name, "unit": unit.code}
    sent = 0
    for user in staff_with(org, "messages.send", unit.property):
        if notify(org, "portal_message_received", user=user, context=context, lease=lease, created_by=message.author,
                  dedupe_key=f"portal_message_received:{message.pk}:{user.pk}"):
            sent += 1
    return sent


def portal_message_reply(message) -> int:
    """The office replied in the portal (D-131): the tenant is told by SMS to open it."""
    lease = _lease(message.lease)
    if notify(lease.organization, "portal_message_reply", tenant=message.tenant,
              context=_tenancy(lease, message.tenant), lease=lease, created_by=message.author,
              dedupe_key=f"portal_message_reply:{message.pk}"):
        return 1
    return 0


def renewal_offered(offer) -> int:
    """A renewal offer was made (D-131): the tenants on the lease are told by SMS to open the portal."""
    lease = _lease(offer.lease)
    rule = effective_rule(lease.organization, catalog.get("renewal_offered"))
    fields = {"start_date": _date(offer.start_date), "rent": format_money(offer.rent, lease.currency),
              "expires_on": _date(offer.expires_on)}
    sent = 0
    for tenant in lease_recipients(lease, include_co_tenants=rule.include_co_tenants):
        if notify(lease.organization, "renewal_offered", tenant=tenant, context={**_tenancy(lease, tenant), **fields},
                  lease=lease, created_by=offer.offered_by, dedupe_key=f"renewal_offered:{offer.pk}:{tenant.pk}"):
            sent += 1
    return sent


def renewal_answered(offer) -> int:
    """The tenant accepted or declined the offer (D-131): staff who can draft leases hear of it."""
    lease = _lease(offer.lease)
    org, unit = lease.organization, lease.unit
    answer = "accepted" if offer.status == offer.Status.ACCEPTED else "declined"
    context = {"tenant_name": offer.responded_by_name, "property": unit.property.name, "unit": unit.code,
               "answer": answer}
    sent = 0
    for user in staff_with(org, "leases.draft", unit.property):
        if notify(org, "renewal_answered", user=user, context=context, lease=lease,
                  dedupe_key=f"renewal_answered:{offer.pk}:{user.pk}"):
            sent += 1
    return sent


def subscription_access(org: Organization, *, lapsed: bool, today: datetime.date | None = None) -> int:
    """The account went read-only, or a payment opened it again: the owners and the roles the owner chose are told
    in the app. Tenants hear nothing. Returns the number of messages written."""
    from accounts.models import Membership

    today = today or timezone.localdate()
    what = (_("The subscription has lapsed, so the account is read-only: you can still see and download "
              "everything, but not add or change records. The owner can pay on the Subscription page.") if lapsed
            else _("The subscription is paid. The account is open again and you can add and change records."))
    chosen = org.lapse_notify_roles.values_list("pk", flat=True)
    members = (Membership.objects.filter(organization=org, is_active=True, user__is_active=True)
               .filter(Q(role__is_owner_role=True) | Q(role__in=list(chosen))).select_related("user"))
    sent = 0
    for member in members:
        if notify(org, "subscription_access", user=member.user, context={"what": str(what)},
                  dedupe_key=f"subscription_access:{'lapsed' if lapsed else 'paid'}:{today}:{member.user_id}"):
            sent += 1
    return sent


def _move_out_reminders(org: Organization, today: datetime.date) -> int:
    ntype = catalog.get("move_out_soon")
    rule = effective_rule(org, ntype)
    if not (rule.enabled and rule.offsets):
        return 0
    sent = 0
    horizon = today + datetime.timedelta(days=max(rule.offsets))
    leases = Lease.objects.filter(organization=org, status=Lease.Status.ACTIVE, move_out_on__gte=today,
                                  move_out_on__lte=horizon).order_by("move_out_on", "pk")
    for lease in leases:
        # The nearest reminder day that has come; a notice given after it already said the day.
        reached = [o for o in rule.offsets if o >= 0 and lease.move_out_on - datetime.timedelta(days=o) <= today]
        if not reached or lease.notice_given_on >= lease.move_out_on - datetime.timedelta(days=min(reached)):
            continue
        offset = min(reached)
        lease = _lease(lease)
        fields = {"move_out_on": _date(lease.move_out_on), "balance": _balance(lease)}
        for tenant in lease_recipients(lease, include_co_tenants=rule.include_co_tenants):
            if notify(org, ntype.codename, tenant=tenant, context={**_tenancy(lease, tenant), **fields}, lease=lease,
                      dedupe_key=f"move_out_soon:{lease.pk}:{lease.move_out_on}:{offset}:{tenant.pk}",
                      send_now=False):
                sent += 1
    return sent


def send_reminders(today: datetime.date | None = None) -> dict[str, int]:
    """Rent due soon, rent overdue and move-out day coming, for every active organization. Part of the daily job.

    Messages are queued; `send_due_messages` sends them (after quiet hours if the job runs at night).
    """
    today = today or timezone.localdate()
    counts = {"rent_due_soon": 0, "rent_overdue": 0, "move_out_soon": 0}
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
        counts["move_out_soon"] += _move_out_reminders(org, today)
    return counts


def _owner_staff(org, owner) -> list:
    """Staff who can run the owner desk and see all of the owner's properties."""
    from accounts.models import Membership
    from accounts.permissions import can
    from reports.statements import fully_sees

    members = (Membership.objects.filter(organization=org, is_active=True, user__is_active=True)
               .select_related("user", "organization", "role"))
    return [m.user for m in members if can(m, "owners.invite_portal") and fully_sees(m, owner)]


def _owner_alert(org, owner, type_codename, key, context, created_by=None) -> int:
    sent = 0
    for user in _owner_staff(org, owner):
        if notify(org, type_codename, user=user, context={"owner_name": owner.name, **context},
                  created_by=created_by, dedupe_key=f"{type_codename}:{key}:{user.pk}"):
            sent += 1
    return sent


def owner_message_received(message) -> int:
    """An owner wrote to the office in the portal (D-211)."""
    return _owner_alert(message.organization, message.owner, "owner_message_received", message.pk, {},
                        created_by=message.author)


def owner_message_reply(message) -> int:
    """The office replied to an owner: the owner is told by SMS to open the portal."""
    owner = message.owner
    if owner.phone and notify(message.organization, "owner_message_reply", phone=owner.phone,
                              context={"owner_name": owner.name}, created_by=message.author,
                              dedupe_key=f"owner_message_reply:{message.pk}"):
        return 1
    return 0


def owner_approval_asked(approval) -> int:
    owner = approval.owner
    amount = format_money(approval.amount, approval.organization.currency) if approval.amount else "-"
    if owner.phone and notify(approval.organization, "owner_approval_asked", phone=owner.phone,
                              context={"owner_name": owner.name, "what": approval.title[:60], "amount": amount},
                              created_by=approval.asked_by, dedupe_key=f"owner_approval_asked:{approval.pk}"):
        return 1
    return 0


def owner_approval_answered(approval) -> int:
    answer = _("approved") if approval.status == "APPROVED" else _("declined")
    if approval.answer_note:
        answer = f"{answer} ({approval.answer_note})"
    return _owner_alert(approval.organization, approval.owner, "owner_approval_answered", approval.pk,
                        {"what": approval.title[:80], "answer": answer}, created_by=approval.answered_by)


def owner_payout_event(org, owner, what) -> int:
    key = f"{timezone.now().timestamp()}"
    return _owner_alert(org, owner, "owner_payout_event", f"{owner.pk}:{key}", {"what": str(what)})
