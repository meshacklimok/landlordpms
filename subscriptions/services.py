"""Plans, invoices, payments and the SMS wallet for platform billing (D-060)."""

import calendar
import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership, Organization
from accounts.permissions import require
from audit import services as audit

from . import entitlements, etims
from .models import (
    Plan,
    PlatformReceipt,
    PlatformSequence,
    SmsWallet,
    SmsWalletEntry,
    Subscription,
    SubscriptionInvoice,
    SubscriptionPayment,
)

Status = Subscription.Status
Interval = Subscription.Interval
CENT = Decimal("0.01")
TRIAL_DAYS = 30
TRIAL_PLAN = "business"
FREE_PLAN = "free"
RENEW_BEFORE_DAYS = 7
GRACE_DAYS = 14


def _money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _require_platform_admin(user) -> None:
    if not (user and user.is_active and user.is_staff):
        raise PermissionDenied(_("Only a Platform Admin can do this."))


# ---------------------------------------------------------------------------
# Dates and numbers
# ---------------------------------------------------------------------------


def period_end(start: datetime.date, interval: str) -> datetime.date:
    """The last day of a period starting on `start`: the day before the same date next month (or year)."""
    months = 12 if interval == Interval.YEARLY else 1
    years, index = divmod(start.month - 1 + months, 12)
    year, month = start.year + years, index + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day) - datetime.timedelta(days=1)


def next_platform_number(key: str, prefix: str, year: int) -> str:
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("next_platform_number() must run inside the issuing transaction.")
    seq, _created = PlatformSequence.objects.select_for_update().get_or_create(key=key, period=str(year))
    value = seq.next_value
    PlatformSequence.objects.filter(pk=seq.pk).update(next_value=value + 1)
    return f"{prefix}-{year}-{value:06d}"


# ---------------------------------------------------------------------------
# The subscription
# ---------------------------------------------------------------------------


def plan(key: str) -> Plan:
    return Plan.objects.get(key=key)


def subscription_for(org: Organization) -> Subscription:
    """The organization's subscription. One without is given the trial (D-060 item 2)."""
    sub = Subscription.objects.select_related("plan").filter(organization=org).first()
    if sub is not None:
        return sub
    try:
        with transaction.atomic():
            return Subscription.objects.create(
                organization=org, plan=plan(TRIAL_PLAN), status=Status.TRIAL,
                trial_ends_on=timezone.localdate() + datetime.timedelta(days=TRIAL_DAYS))
    except IntegrityError:
        return Subscription.objects.select_related("plan").get(organization=org)


def open_invoice(sub: Subscription) -> SubscriptionInvoice | None:
    return sub.invoices.filter(status=SubscriptionInvoice.Status.OPEN).order_by("-issued_on", "-pk").first()


def self_serve_plans():
    return Plan.objects.filter(is_active=True, self_serve=True)


def _vat(net: Decimal) -> tuple[Decimal, Decimal]:
    if not getattr(settings, "PLATFORM_VAT_REGISTERED", False):
        return Decimal("0"), Decimal("0")
    rate = Decimal(str(getattr(settings, "PLATFORM_VAT_RATE", "16")))
    return rate, _money(net * rate / 100)


def unused_credit(sub: Subscription, today: datetime.date) -> Decimal:
    """The unused days of the current paid period, at the price paid for it."""
    if sub.status != Status.ACTIVE or not sub.period_start or not sub.period_end or sub.period_end < today:
        return Decimal("0")
    last = (sub.invoices.filter(status=SubscriptionInvoice.Status.PAID, period_start=sub.period_start)
            .order_by("-pk").first())
    if last is None or not last.net_amount:
        return Decimal("0")
    days = (sub.period_end - sub.period_start).days + 1
    left = (sub.period_end - today).days  # today is used
    return _money(last.price * left / days) if left > 0 else Decimal("0")


def issue_invoice(sub: Subscription, new_plan: Plan, interval: str, *, reason: str, today: datetime.date,
                  period_start=None, credit=Decimal("0"), actor=None) -> SubscriptionInvoice:
    org = sub.organization
    price = _money(new_plan.price(interval))
    credit = min(_money(credit), price)
    net = price - credit
    rate, vat = _vat(net)
    with transaction.atomic():
        invoice = SubscriptionInvoice.objects.create(
            organization=org, subscription=sub, reason=reason,
            number=next_platform_number("invoice", "LPM-INV", today.year),
            plan=new_plan, plan_name=new_plan.name, interval=interval,
            period_start=period_start, period_end=period_end(period_start, interval) if period_start else None,
            customer_name=org.name, customer_kra_pin=org.kra_pin, customer_email=org.billing_email,
            price=price, credit=credit, net_amount=net, vat_rate=rate, vat_amount=vat, total=net + vat,
            issued_on=today, due_on=(period_start - datetime.timedelta(days=1)) if period_start else today,
            created_by=actor)
        reference = etims.get_adapter().submit(invoice)
        if reference:
            invoice.etims_reference = reference
            invoice.save(update_fields=["etims_reference"])
        audit.record("subscription_invoice.issue", actor=actor, organization=org, obj=invoice,
                     changes={"plan": new_plan.name, "interval": interval, "total": str(invoice.total)})
    return invoice


def _void_open(sub: Subscription, reason: str, actor=None) -> None:
    for invoice in sub.invoices.filter(status=SubscriptionInvoice.Status.OPEN, payments__isnull=True):
        invoice.status = SubscriptionInvoice.Status.VOID
        invoice.void_reason = reason
        invoice.voided_at = timezone.now()
        invoice.save(update_fields=["status", "void_reason", "voided_at", "updated_at"])
        audit.record("subscription_invoice.void", actor=actor, organization=sub.organization, obj=invoice,
                     changes={"reason": reason})


@transaction.atomic
def choose_plan(actor: Membership, new_plan: Plan, interval: str, *, request=None,
                today: datetime.date | None = None) -> SubscriptionInvoice | None:
    """Free applies at once; a paid plan gets an invoice and starts when it is paid (D-060 item 5)."""
    require(actor, "subscription.manage")
    today = today or timezone.localdate()
    org = actor.organization
    sub = Subscription.objects.select_for_update().get(pk=subscription_for(org).pk)
    if not new_plan.is_active or not new_plan.self_serve:
        raise ValidationError(_("That plan is not available. Contact us."))
    if interval not in Interval.values:
        raise ValidationError(_("Choose monthly or yearly."))
    over = entitlements.over_limits(org, new_plan)
    if over:
        raise ValidationError(_("You use more than %(plan)s allows: %(over)s. Remove some first, or choose a "
                                "bigger plan.") % {"plan": new_plan.name, "over": "; ".join(over)})
    if new_plan.is_free:
        _void_open(sub, _("Free plan chosen"), actor.user)
        before = sub.plan.name
        sub.plan, sub.interval, sub.status = new_plan, Interval.MONTHLY, Status.ACTIVE
        sub.trial_ends_on = sub.period_start = sub.period_end = sub.past_due_since = None
        sub.save()
        _set_org_active(org, actor.user)
        audit.record("subscription.change_plan", actor=actor.user, organization=org, obj=sub, request=request,
                     changes={"plan": [before, new_plan.name]})
        return None
    if sub.status == Status.ACTIVE and sub.plan_id == new_plan.pk and sub.interval == interval:
        raise ValidationError(_("You are already on this plan."))
    _void_open(sub, _("Another plan chosen"), actor.user)
    return issue_invoice(sub, new_plan, interval, reason=SubscriptionInvoice.Reason.NEW_PLAN, today=today,
                         credit=unused_credit(sub, today), actor=actor.user)


def _set_org_active(org: Organization, actor=None) -> None:
    if org.status == Organization.Status.READ_ONLY:
        org.status = Organization.Status.ACTIVE
        org.save(update_fields=["status", "updated_at"])
        audit.record("organization.reactivate", actor=actor, organization=org, obj=org)


def _set_org_read_only(org: Organization) -> None:
    if org.status == Organization.Status.ACTIVE:
        org.status = Organization.Status.READ_ONLY
        org.save(update_fields=["status", "updated_at"])
        audit.record("organization.read_only", organization=org, obj=org, changes={"reason": "subscription lapsed"})


# ---------------------------------------------------------------------------
# Payments (recorded by a Platform Admin until our Paybill is live, D-060 item 7)
# ---------------------------------------------------------------------------


def _receipt(payment: SubscriptionPayment) -> PlatformReceipt:
    return PlatformReceipt.objects.create(
        payment=payment, number=next_platform_number("receipt", "LPM-RCT", timezone.localdate().year))


def check_payment(amount, reference: str, paid_on: datetime.date) -> str:
    if amount is None or amount <= 0:
        raise ValidationError({"amount": _("Enter an amount above zero.")})
    reference = (reference or "").strip().upper()
    if not reference:
        raise ValidationError({"reference": _("Enter the payment reference.")})
    if SubscriptionPayment.objects.filter(reference=reference).exists():
        raise ValidationError({"reference": _("That reference has already been recorded.")})
    if paid_on > timezone.localdate():
        raise ValidationError({"paid_on": _("The date cannot be in the future.")})
    return reference


@transaction.atomic
def record_payment(admin, invoice: SubscriptionInvoice, *, amount, method: str, reference: str,
                   paid_on: datetime.date, note: str = "", request=None) -> SubscriptionPayment:
    _require_platform_admin(admin)
    invoice = SubscriptionInvoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != SubscriptionInvoice.Status.OPEN:
        raise ValidationError(_("That invoice is not open."))
    reference = check_payment(amount, reference, paid_on)
    payment = SubscriptionPayment.objects.create(
        organization=invoice.organization, invoice=invoice, amount=_money(amount), method=method,
        reference=reference, paid_on=paid_on, note=note, recorded_by=admin)
    receipt = _receipt(payment)
    audit.record("subscription_payment.record", actor=admin, organization=invoice.organization, obj=payment,
                 request=request, changes={"invoice": invoice.number, "amount": str(payment.amount),
                                           "receipt": receipt.number})
    if invoice.paid >= invoice.total:
        _settle(invoice, paid_on, admin)
    return payment


def _settle(invoice: SubscriptionInvoice, paid_on: datetime.date, actor) -> None:
    invoice.status = SubscriptionInvoice.Status.PAID
    invoice.paid_at = timezone.now()
    sub = Subscription.objects.select_for_update().get(pk=invoice.subscription_id)
    if invoice.period_start is None:
        # A new plan starts on the payment date.
        invoice.period_start = paid_on
        invoice.period_end = period_end(paid_on, invoice.interval)
    invoice.save(update_fields=["status", "paid_at", "period_start", "period_end", "updated_at"])
    before = (sub.plan.name, sub.status)
    sub.plan_id, sub.interval, sub.status = invoice.plan_id, invoice.interval, Status.ACTIVE
    # A renewal keeps its own dates, even when paid late or early.
    sub.period_start, sub.period_end = invoice.period_start, invoice.period_end
    sub.trial_ends_on = sub.past_due_since = None
    sub.save()
    _set_org_active(sub.organization, actor)
    audit.record("subscription.activate", actor=actor, organization=sub.organization, obj=sub,
                 changes={"plan": [before[0], invoice.plan_name], "status": [before[1], Status.ACTIVE],
                          "period_end": [None, str(sub.period_end)]})


@transaction.atomic
def void_invoice(admin, invoice: SubscriptionInvoice, reason: str, request=None) -> None:
    _require_platform_admin(admin)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(_("Give a reason."))
    invoice = SubscriptionInvoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != SubscriptionInvoice.Status.OPEN or invoice.payments.exists():
        raise ValidationError(_("Only an open invoice with no payments can be voided."))
    invoice.status = SubscriptionInvoice.Status.VOID
    invoice.void_reason = reason
    invoice.voided_at = timezone.now()
    invoice.save(update_fields=["status", "void_reason", "voided_at", "updated_at"])
    audit.record("subscription_invoice.void", actor=admin, organization=invoice.organization, obj=invoice,
                 request=request, changes={"reason": reason})


# ---------------------------------------------------------------------------
# SMS wallet (D-060 item 10)
# ---------------------------------------------------------------------------


def sms_price() -> Decimal:
    return Decimal(str(getattr(settings, "SMS_PRICE", "1.00")))


def sms_balance(org: Organization) -> Decimal:
    wallet = SmsWallet.objects.filter(organization=org).first()
    return wallet.balance if wallet else Decimal("0")


def _wallet(org: Organization) -> SmsWallet:
    SmsWallet.objects.get_or_create(organization=org)
    return SmsWallet.objects.select_for_update().get(organization=org)


def _entry(org, kind, amount: Decimal, **fields) -> SmsWalletEntry:
    wallet = _wallet(org)
    wallet.balance += amount
    wallet.save(update_fields=["balance"])
    return SmsWalletEntry.objects.create(organization=org, kind=kind, amount=amount, balance_after=wallet.balance,
                                         **fields)


@transaction.atomic
def top_up_sms(admin, org: Organization, *, amount, method: str, reference: str, paid_on: datetime.date,
               note: str = "", request=None) -> SmsWalletEntry:
    _require_platform_admin(admin)
    reference = check_payment(amount, reference, paid_on)
    payment = SubscriptionPayment.objects.create(organization=org, amount=_money(amount), method=method,
                                                 reference=reference, paid_on=paid_on, note=note, recorded_by=admin)
    receipt = _receipt(payment)
    entry = _entry(org, SmsWalletEntry.Kind.TOP_UP, payment.amount, payment=payment, note=note, created_by=admin)
    audit.record("sms_wallet.top_up", actor=admin, organization=org, obj=payment, request=request,
                 changes={"amount": str(payment.amount), "receipt": receipt.number})
    return entry


@transaction.atomic
def adjust_sms(admin, org: Organization, amount, note: str, request=None) -> SmsWalletEntry:
    _require_platform_admin(admin)
    if not (note or "").strip():
        raise ValidationError(_("Give a reason."))
    entry = _entry(org, SmsWalletEntry.Kind.ADJUSTMENT, _money(amount), note=note.strip(), created_by=admin)
    audit.record("sms_wallet.adjust", actor=admin, organization=org, obj=org, request=request,
                 changes={"amount": str(entry.amount), "note": entry.note})
    return entry


def segments(body: str) -> int:
    n = len(body or "")
    return 0 if not n else 1 if n <= 160 else -(-n // 153)


def charge_message(message) -> SmsWalletEntry | None:
    """Charges a sent SMS to its organization's wallet. Called inside the sending transaction."""
    if not getattr(settings, "SMS_WALLET_ENFORCED", False):
        return None
    amount = sms_price() * segments(message.body)
    if not amount:
        return None
    return _entry(message.organization, SmsWalletEntry.Kind.MESSAGE, -_money(amount), message=message)


def sms_available(org: Organization) -> bool:
    if not getattr(settings, "SMS_WALLET_ENFORCED", False):
        return True
    return sms_balance(org) > 0


# ---------------------------------------------------------------------------
# The daily job (D-060 item 6)
# ---------------------------------------------------------------------------


def daily(today: datetime.date | None = None) -> dict[str, int]:
    today = today or timezone.localdate()
    counts = {"trial_to_free": 0, "trial_ended": 0, "renewals": 0, "past_due": 0, "lapsed": 0}
    free = plan(FREE_PLAN)
    for sub in Subscription.objects.select_related("organization", "plan"):
        with transaction.atomic():
            sub = Subscription.objects.select_for_update().select_related("organization", "plan").get(pk=sub.pk)
            key = _daily_one(sub, today, free)
            if key:
                counts[key] += 1
    return counts


def _daily_one(sub: Subscription, today: datetime.date, free: Plan) -> str | None:
    org = sub.organization
    if sub.status == Status.TRIAL and sub.trial_ends_on and sub.trial_ends_on < today:
        if not entitlements.over_limits(org, free):
            sub.plan, sub.status, sub.trial_ends_on = free, Status.ACTIVE, None
            sub.save()
            audit.record("subscription.trial_to_free", organization=org, obj=sub)
            return "trial_to_free"
        sub.status, sub.past_due_since = Status.PAST_DUE, today
        sub.save()
        if open_invoice(sub) is None:
            issue_invoice(sub, sub.plan, Interval.MONTHLY, reason=SubscriptionInvoice.Reason.TRIAL_END, today=today)
        audit.record("subscription.past_due", organization=org, obj=sub, changes={"reason": "trial ended"})
        return "trial_ended"
    if sub.status == Status.ACTIVE and sub.period_end and not sub.plan.is_free:
        renewal_start = sub.period_end + datetime.timedelta(days=1)
        renewed = sub.invoices.filter(reason=SubscriptionInvoice.Reason.RENEWAL, period_start=renewal_start).exclude(
            status=SubscriptionInvoice.Status.VOID).exists()
        if not renewed and (sub.period_end - today).days < RENEW_BEFORE_DAYS:
            issue_invoice(sub, sub.plan, sub.interval, reason=SubscriptionInvoice.Reason.RENEWAL, today=today,
                          period_start=renewal_start)
        if sub.period_end < today:
            sub.status, sub.past_due_since = Status.PAST_DUE, today
            sub.save()
            audit.record("subscription.past_due", organization=org, obj=sub, changes={"reason": "not renewed"})
            return "past_due"
        return None if renewed or (sub.period_end - today).days >= RENEW_BEFORE_DAYS else "renewals"
    if sub.status == Status.PAST_DUE and sub.past_due_since and (today - sub.past_due_since).days >= GRACE_DAYS:
        sub.status = Status.LAPSED
        sub.save()
        _set_org_read_only(org)
        audit.record("subscription.lapse", organization=org, obj=sub)
        return "lapsed"
    return None


# ---------------------------------------------------------------------------
# What the page and the banner show
# ---------------------------------------------------------------------------


def banner(org: Organization, today: datetime.date | None = None) -> dict | None:
    """A top-bar warning, or None (D-060 item 12)."""
    if org is None:
        return None
    today = today or timezone.localdate()
    sub = subscription_for(org)
    if sub.status == Status.TRIAL and sub.trial_ends_on and (sub.trial_ends_on - today).days < 7:
        days = max((sub.trial_ends_on - today).days, 0)
        return {"level": "info", "text": _("Your free trial ends in %(n)s days. Choose a plan to keep going.")
                % {"n": days}}
    if sub.status == Status.PAST_DUE:
        left = GRACE_DAYS - (today - (sub.past_due_since or today)).days
        return {"level": "warning", "text": _("Your subscription payment is due. In %(n)s days the account "
                                              "becomes read-only.") % {"n": max(left, 0)}}
    if sub.status == Status.LAPSED:
        return {"level": "danger", "text": _("Your subscription has lapsed. Pay to start creating again.")}
    return None
