"""Plans, invoices, payments and the SMS wallet for platform billing (D-060)."""

import calendar
import datetime
import logging
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership, Organization
from accounts.permissions import can, require
from audit import services as audit
from notifications import catalog

from . import entitlements, etims
from .models import (
    BillingPolicy,
    Plan,
    PlatformReceipt,
    PlatformSequence,
    PlatformTransaction,
    SmsWallet,
    SmsWalletEntry,
    Subscription,
    SubscriptionInvoice,
    SubscriptionPayment,
    SubscriptionReminder,
)

logger = logging.getLogger(__name__)

Status = Subscription.Status
Interval = Subscription.Interval
CENT = Decimal("0.01")
# The figures in force are the ones in BillingPolicy (Django admin), read with the functions below; these are
# the defaults it starts with.
TRIAL_DAYS = 30
TRIAL_PLAN = "business"
FREE_PLAN = "free"
RENEW_BEFORE_DAYS = 7
GRACE_DAYS = 14


def policy() -> BillingPolicy:
    return BillingPolicy.get()


def trial_days() -> int:
    return policy().trial_days


def grace_days() -> int:
    return policy().grace_days


def _money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _require_platform_admin(user) -> None:
    if not (user and user.is_active and user.is_staff):
        raise PermissionDenied(_("Only a Platform Admin can do this."))


# ---------------------------------------------------------------------------
# Dates and numbers
# ---------------------------------------------------------------------------


def period_end(start: datetime.date, interval: str, years: int = 1) -> datetime.date:
    """The last day of a period starting on `start`: the day before the same date next month (or year).
    A one-off period lasts `years` years (the plan's `one_off_years`)."""
    months = {Interval.YEARLY: 12, Interval.ONCE: 12 * years}.get(interval, 1)
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
                trial_ends_on=timezone.localdate() + datetime.timedelta(days=trial_days()))
    except IntegrityError:
        return Subscription.objects.select_related("plan").get(organization=org)


def open_invoice(sub: Subscription) -> SubscriptionInvoice | None:
    return sub.invoices.filter(status=SubscriptionInvoice.Status.OPEN).order_by("-issued_on", "-pk").first()


def self_serve_plans():
    return Plan.objects.filter(is_active=True, self_serve=True)


def fitting_plan(org: Organization) -> Plan | None:
    """The first plan, in the order set in admin, that a paying landlord can choose and that holds what the
    organization uses now (used when a trial ends and no free plan is on)."""
    for candidate in self_serve_plans():
        if not candidate.is_free and not entitlements.over_limits(org, candidate):
            return candidate
    return None


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
    from growth import referrals

    price = _money(new_plan.price(interval))
    credit = min(_money(credit), price)
    with transaction.atomic():
        # A referral credit (D-133) is a percent off what is left after the unused days; one per invoice.
        percent = referrals.next_credit_percent(org) if price > credit else 0
        discount = _money((price - credit) * percent / 100)
        net = price - credit - discount
        rate, vat = _vat(net)
        invoice = SubscriptionInvoice.objects.create(
            organization=org, subscription=sub, reason=reason,
            number=next_platform_number("invoice", "LPM-INV", today.year),
            plan=new_plan, plan_name=new_plan.name, interval=interval,
            period_start=period_start,
            period_end=period_end(period_start, interval, new_plan.one_off_years) if period_start else None,
            customer_name=org.name, customer_kra_pin=org.kra_pin, customer_email=org.billing_email,
            price=price, credit=credit, discount=discount, net_amount=net, vat_rate=rate, vat_amount=vat,
            total=net + vat,
            issued_on=today, due_on=(period_start - datetime.timedelta(days=1)) if period_start else today,
            created_by=actor)
        if discount:
            referrals.take_credit(org, invoice)
        reference = etims.get_adapter().submit(invoice)
        if reference:
            invoice.etims_reference = reference
            invoice.save(update_fields=["etims_reference"])
        audit.record("subscription_invoice.issue", actor=actor, organization=org, obj=invoice,
                     changes={"plan": new_plan.name, "interval": interval, "total": str(invoice.total)})
    return invoice


def _release_credit(invoice: SubscriptionInvoice) -> None:
    """A voided invoice gives its referral credit back for the next one."""
    from growth.models import ReferralCredit

    ReferralCredit.objects.filter(invoice=invoice).update(invoice=None, used_at=None, updated_at=timezone.now())


def _void_open(sub: Subscription, reason: str, actor=None) -> None:
    for invoice in sub.invoices.filter(status=SubscriptionInvoice.Status.OPEN, payments__isnull=True):
        invoice.status = SubscriptionInvoice.Status.VOID
        invoice.void_reason = reason
        invoice.voided_at = timezone.now()
        invoice.save(update_fields=["status", "void_reason", "voided_at", "updated_at"])
        _release_credit(invoice)
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
        raise ValidationError(_("Choose monthly, yearly or one-off."))
    if interval == Interval.ONCE and not new_plan.has_one_off:
        raise ValidationError(_("The %(plan)s plan has no one-off price. Choose monthly or yearly.")
                              % {"plan": new_plan.name})
    over = entitlements.over_limits(org, new_plan)
    if over:
        raise ValidationError(_("You use more than %(plan)s allows: %(over)s. Remove some first, or choose a "
                                "bigger plan.") % {"plan": new_plan.name, "over": "; ".join(over)})
    if _is_downgrade(sub, new_plan, today):
        return _schedule_change(sub, new_plan, interval, actor, request)
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
        if sub.next_plan_id:
            cancel_scheduled_change(actor, request=request)
            return None
        raise ValidationError(_("You are already on this plan."))
    sub.next_plan, sub.next_interval = None, ""
    sub.save(update_fields=["next_plan", "next_interval", "updated_at"])
    _void_open(sub, _("Another plan chosen"), actor.user)
    return issue_invoice(sub, new_plan, interval, reason=SubscriptionInvoice.Reason.NEW_PLAN, today=today,
                         credit=unused_credit(sub, today), actor=actor.user)


def _is_downgrade(sub: Subscription, new_plan: Plan, today: datetime.date) -> bool:
    """A cheaper plan, or Free, chosen while a paid period is running waits for the period's end, so what was
    paid for is used (D-165). An upgrade, a trial or a late account starts at once."""
    return bool(sub.status == Status.ACTIVE and sub.period_end and sub.period_end >= today and not sub.plan.is_free
                and (new_plan.is_free or new_plan.monthly_price < sub.plan.monthly_price))


def _schedule_change(sub: Subscription, new_plan: Plan, interval: str, actor: Membership, request=None) -> None:
    before = sub.next_plan.name if sub.next_plan_id else None
    sub.next_plan, sub.next_interval = new_plan, Interval.MONTHLY if new_plan.is_free else interval
    sub.save(update_fields=["next_plan", "next_interval", "updated_at"])
    audit.record("subscription.change_scheduled", actor=actor.user, organization=sub.organization, obj=sub,
                 request=request, changes={"next_plan": [before, new_plan.name],
                                           "starts_on": str(sub.period_end + datetime.timedelta(days=1))})


@transaction.atomic
def cancel_scheduled_change(actor: Membership, request=None) -> None:
    """Drops a downgrade or "stop renewing" that was waiting for the end of the period."""
    require(actor, "subscription.manage")
    sub = Subscription.objects.select_for_update().get(pk=subscription_for(actor.organization).pk)
    if not sub.next_plan_id:
        return
    name = sub.next_plan.name
    sub.next_plan, sub.next_interval = None, ""
    sub.save(update_fields=["next_plan", "next_interval", "updated_at"])
    audit.record("subscription.change_cancelled", actor=actor.user, organization=sub.organization, obj=sub,
                 request=request, changes={"next_plan": [name, None]})


def scheduled_change(sub: Subscription) -> dict | None:
    """What the page shows about a change waiting for the period's end."""
    if not sub.next_plan_id or not sub.period_end:
        return None
    return {"plan": sub.next_plan, "interval": sub.next_interval, "on": sub.period_end + datetime.timedelta(days=1),
            "stops": sub.next_plan.is_free}


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
# Payments: recorded by a Platform Admin, or confirmed by Safaricom or Paystack (collect.py, D-126)
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
    return apply_payment(invoice, amount=amount, method=method, reference=reference, paid_on=paid_on, note=note,
                         admin=admin, request=request)


def apply_payment(invoice: SubscriptionInvoice, *, amount, method: str, reference: str, paid_on: datetime.date,
                  note: str = "", admin=None, request=None) -> SubscriptionPayment:
    """Records money against an open invoice, with a receipt; the plan starts once it is paid in full.
    `admin` is None for a payment Safaricom or Paystack confirmed (D-126). Runs inside a transaction."""
    invoice = SubscriptionInvoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != SubscriptionInvoice.Status.OPEN:
        raise ValidationError(_("That invoice is not open."))
    reference = check_payment(amount, reference, paid_on)
    payment = SubscriptionPayment.objects.create(
        organization=invoice.organization, invoice=invoice, amount=_money(amount), method=method,
        reference=reference, paid_on=paid_on, note=note, recorded_by=admin)
    receipt = _receipt(payment)
    _claim_transaction(payment, invoice)
    audit.record("subscription_payment.record", actor=admin, organization=invoice.organization, obj=payment,
                 request=request, changes={"invoice": invoice.number, "amount": str(payment.amount),
                                           "receipt": receipt.number, "method": method})
    if invoice.paid >= invoice.total:
        _settle(invoice, paid_on, admin)
    return payment


def _claim_transaction(payment: SubscriptionPayment, invoice: SubscriptionInvoice | None = None) -> None:
    """A payment recorded by hand with the code of one that came in unmatched (D-126) settles it."""
    PlatformTransaction.objects.filter(code=payment.reference, status=PlatformTransaction.Status.UNMATCHED).update(
        status=PlatformTransaction.Status.APPLIED, payment=payment, invoice=invoice)


def _settle(invoice: SubscriptionInvoice, paid_on: datetime.date, actor) -> None:
    invoice.status = SubscriptionInvoice.Status.PAID
    invoice.paid_at = timezone.now()
    sub = Subscription.objects.select_for_update().get(pk=invoice.subscription_id)
    if invoice.period_start is None:
        # A new plan starts on the payment date.
        invoice.period_start = paid_on
        invoice.period_end = period_end(paid_on, invoice.interval, invoice.plan.one_off_years)
    invoice.save(update_fields=["status", "paid_at", "period_start", "period_end", "updated_at"])
    before = (sub.plan.name, sub.status)
    sub.plan_id, sub.interval, sub.status = invoice.plan_id, invoice.interval, Status.ACTIVE
    # A renewal keeps its own dates, even when paid late or early.
    sub.period_start, sub.period_end = invoice.period_start, invoice.period_end
    sub.trial_ends_on = sub.past_due_since = sub.grace_until = None
    sub.next_plan, sub.next_interval = None, ""
    sub.save()
    _set_org_active(sub.organization, actor)
    if invoice.total > 0:
        from growth import referrals  # the first payment of a referred organization rewards both (D-133)

        referrals.on_payment(sub.organization)
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
    _release_credit(invoice)
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


SMS_TOP_UP_MIN = Decimal("100")
SMS_TOP_UP_MAX = Decimal("50000")


@transaction.atomic
def top_up_sms(admin, org: Organization, *, amount, method: str, reference: str, paid_on: datetime.date,
               note: str = "", request=None) -> SmsWalletEntry:
    _require_platform_admin(admin)
    return apply_sms_top_up(org, amount=amount, method=method, reference=reference, paid_on=paid_on, note=note,
                            admin=admin, request=request)


def apply_sms_top_up(org: Organization, *, amount, method: str, reference: str, paid_on: datetime.date,
                     note: str = "", admin=None, request=None) -> SmsWalletEntry:
    """Credits the wallet with money received, with a receipt. `admin` is None for a payment Safaricom or
    Paystack confirmed (D-142). Runs inside a transaction."""
    reference = check_payment(amount, reference, paid_on)
    payment = SubscriptionPayment.objects.create(organization=org, amount=_money(amount), method=method,
                                                 reference=reference, paid_on=paid_on, note=note, recorded_by=admin)
    receipt = _receipt(payment)
    _claim_transaction(payment)
    entry = _entry(org, SmsWalletEntry.Kind.TOP_UP, payment.amount, payment=payment, note=note, created_by=admin)
    # Credit is back, so the "low" and "out" warnings may be sent again.
    SmsWallet.objects.filter(organization=org, alert_level__gt=ALERT_USED_UP).update(alert_level=ALERT_USED_UP)
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


def month_start(today: datetime.date | None = None) -> datetime.datetime:
    """The first moment of the calendar month, in the organization's clock (Nairobi)."""
    today = today or timezone.localdate()
    return timezone.make_aware(datetime.datetime.combine(today.replace(day=1), datetime.time.min))


def sms_parts_used(org: Organization, today: datetime.date | None = None) -> int:
    """SMS parts the organization sent this month, whoever paid for them."""
    total = SmsWalletEntry.objects.filter(organization=org, kind=SmsWalletEntry.Kind.MESSAGE,
                                          created_at__gte=month_start(today)).aggregate(n=Sum("segments"))["n"]
    return total or 0


def _wallet_row(org: Organization) -> SmsWallet:
    """The organization's wallet, or the defaults (sponsored) for one that has not sent anything yet."""
    return SmsWallet.objects.filter(organization=org).first() or SmsWallet(organization=org)


def sms_summary(org: Organization) -> dict:
    """What the subscription page shows about SMS: who pays, parts used and left this month, the balance."""
    wallet = _wallet_row(org)
    used = sms_parts_used(org)
    cap = wallet.monthly_cap
    if wallet.mode == SmsWallet.Mode.PREPAID:
        cap = None
    labels = dict(catalog.CHOICES)
    by_type = (SmsWalletEntry.objects.filter(organization=org, kind=SmsWalletEntry.Kind.MESSAGE,
                                             created_at__gte=month_start(), message__isnull=False)
               .values("message__type").annotate(parts=Sum("segments")).order_by("-parts"))
    return {
        "mode": wallet.mode,
        "mode_label": wallet.get_mode_display(),
        "used": used,
        "cap": cap,
        "left": None if cap is None else max(0, cap - used),
        "percent": 0 if not cap else min(100, round(used * 100 / cap)),
        "balance": wallet.balance,
        "price": sms_price(),
        "available": sms_available(org),
        "by_type": [(labels.get(row["message__type"], row["message__type"]), row["parts"]) for row in by_type],
    }


ALERT_NEARLY = 1      # 80% of the month's parts used
ALERT_USED_UP = 2     # the month's parts used up
ALERT_LOW = 3         # the wallet will not cover many more parts
ALERT_EMPTY = 4       # the wallet is empty
LOW_CREDIT_PARTS = 20


def _alert_level(wallet: SmsWallet, used: int) -> int:
    """The most serious warning that applies now: 0 for none, else one of the ALERT_ levels."""
    level = 0
    cap = wallet.monthly_cap
    if wallet.mode != SmsWallet.Mode.PREPAID and cap:
        level = ALERT_USED_UP if used >= cap else ALERT_NEARLY if used * 100 >= cap * 80 else 0
    paying = wallet.mode == SmsWallet.Mode.PREPAID or (wallet.mode == SmsWallet.Mode.BUNDLED and used >= (cap or 0))
    if paying:
        if wallet.balance <= 0:
            level = ALERT_EMPTY
        elif wallet.balance < sms_price() * LOW_CREDIT_PARTS:
            level = max(level, ALERT_LOW)
    return level


def _raise_alert(wallet: SmsWallet, used: int) -> None:
    """Sends each warning once a month. The wallet row is locked by the caller."""
    month = timezone.localdate().replace(day=1)
    if wallet.alert_month != month:
        wallet.alert_month, wallet.alert_level = month, 0
    level = _alert_level(wallet, used)
    if level > wallet.alert_level:
        wallet.alert_level = level
        org_pk, mode = wallet.organization_id, wallet.mode
        transaction.on_commit(lambda: notify_sms_level(org_pk, level, mode))
    wallet.save(update_fields=["alert_month", "alert_level"])


def notify_sms_level(org_pk: int, level: int, mode: str) -> None:
    """Emails the organization's billing contacts, and us when a sponsored cap is reached. Never raises."""
    from django.core.mail import send_mail

    from ops.alerts import recipients
    from notifications.providers.mail import SYSTEM, mailer_for

    try:
        org = Organization.objects.get(pk=org_pk)
        wallet, used = _wallet_row(org), sms_parts_used(org)
        sponsored = mode == SmsWallet.Mode.SPONSORED
        used_up = (_("You have used this month's %(cap)s SMS parts. Messages go by email where possible until next "
                     "month. Reply to ask us to raise the limit.") if sponsored else
                   _("You have used the %(cap)s SMS parts included this month. From now on SMS are taken from your "
                     "SMS credit."))
        text = {
            ALERT_NEARLY: _("You have used %(used)s of your %(cap)s SMS parts this month."),
            ALERT_USED_UP: used_up,
            ALERT_LOW: _("Your SMS credit is low: KES %(balance)s."),
            ALERT_EMPTY: _("Your SMS credit has run out. Messages go by email where possible until you top up."),
        }[level] % {"used": used, "cap": wallet.monthly_cap, "balance": f"{wallet.balance:,.2f}"}
        to = ([org.billing_email] if org.billing_email else
              [m.user.email for m in Membership.objects.filter(organization=org, is_active=True, user__is_active=True)
               .select_related("user", "role") if m.user.email and can(m, "subscription.manage")])
        if to:
            send_mail(_("[NyumbaPMS] SMS: %(org)s") % {"org": org.name},
                      text + "\n\n" + _("See and top up on the Subscription page."), None, sorted(set(to)),
                      using=mailer_for(SYSTEM))
        if sponsored and level == ALERT_USED_UP and recipients():
            send_mail(f"[NyumbaPMS] {org.name} reached its SMS cap",
                      f"{org.name} used {used} of {wallet.monthly_cap} sponsored parts. Raise the cap under SMS "
                      "wallets in admin if you want them to keep sending.", None, recipients(),
                      using=mailer_for(SYSTEM))
    except Exception:
        logger.exception("SMS alert level %s for organization %s failed", level, org_pk)


def charge_message(message) -> SmsWalletEntry | None:
    """Records a sent SMS against its organization and charges the wallet if the mode says so. Called inside the
    sending transaction. Sponsored parts are recorded at no charge; bundled parts past the monthly allowance and
    all prepaid parts cost `SMS_PRICE` a part, taken only when the wallet is enforced. Login codes and security
    alerts do not come through here: they are never counted or charged (D-142)."""
    parts = segments(message.body)
    if not parts:
        return None
    org = message.organization
    wallet = _wallet(org)
    amount = Decimal("0")
    if getattr(settings, "SMS_WALLET_ENFORCED", False):
        if wallet.mode == SmsWallet.Mode.PREPAID:
            amount = sms_price() * parts
        elif wallet.mode == SmsWallet.Mode.BUNDLED:
            included_left = max(0, (wallet.monthly_cap or 0) - sms_parts_used(org))
            amount = sms_price() * max(0, parts - included_left)
    entry = _entry(org, SmsWalletEntry.Kind.MESSAGE, -_money(amount), message=message, segments=parts)
    wallet.refresh_from_db()
    _raise_alert(wallet, sms_parts_used(org))
    return entry


def sms_available(org: Organization) -> bool:
    """Whether the organization may send another SMS now. Always true while the wallet is not enforced."""
    if not getattr(settings, "SMS_WALLET_ENFORCED", False):
        return True
    wallet = _wallet_row(org)
    if wallet.mode == SmsWallet.Mode.PREPAID:
        return wallet.balance > 0
    under = wallet.monthly_cap is None or sms_parts_used(org) < wallet.monthly_cap
    if wallet.mode == SmsWallet.Mode.BUNDLED:
        return under or wallet.balance > 0
    return under


# ---------------------------------------------------------------------------
# The daily job (D-060 item 6)
# ---------------------------------------------------------------------------


def daily(today: datetime.date | None = None) -> dict[str, int]:
    today = today or timezone.localdate()
    counts = {"trial_to_free": 0, "trial_ended": 0, "renewals": 0, "past_due": 0, "lapsed": 0}
    # Free is a plan like any other: it exists only while a Platform Admin has it switched on.
    free = Plan.objects.filter(key=FREE_PLAN, is_active=True).first()
    for sub in Subscription.objects.select_related("organization", "plan"):
        with transaction.atomic():
            sub = (Subscription.objects.select_for_update(of=("self",)).select_related("organization", "plan")
                   .get(pk=sub.pk))
            key = _daily_one(sub, today, free)
            if key:
                counts[key] += 1
    return counts


def _daily_one(sub: Subscription, today: datetime.date, free: Plan | None) -> str | None:
    org = sub.organization
    if sub.status == Status.TRIAL and sub.trial_ends_on and sub.trial_ends_on < today:
        if free is not None and not entitlements.over_limits(org, free):
            sub.plan, sub.status, sub.trial_ends_on = free, Status.ACTIVE, None
            sub.save()
            audit.record("subscription.trial_to_free", organization=org, obj=sub)
            return "trial_to_free"
        # The first invoice is for the smallest band that holds what they use.
        sub.plan = fitting_plan(org) or sub.plan
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
        target, interval = _renewal_target(sub)
        if target is None:
            # The landlord asked not to renew: the paid period runs out, then they are on Free.
            return _stop_renewing(sub, free) if sub.period_end < today and free is not None else None
        lead = policy().invoice_lead_days
        if not renewed and (sub.period_end - today).days < lead:
            issue_invoice(sub, target, interval, reason=SubscriptionInvoice.Reason.RENEWAL, today=today,
                          period_start=renewal_start)
        if sub.period_end < today:
            sub.status, sub.past_due_since = Status.PAST_DUE, today
            sub.save()
            audit.record("subscription.past_due", organization=org, obj=sub, changes={"reason": "not renewed"})
            return "past_due"
        return None if renewed or (sub.period_end - today).days >= lead else "renewals"
    if sub.status == Status.PAST_DUE and sub.past_due_since and _grace_over(sub, today):
        sub.status = Status.LAPSED
        sub.save()
        _set_org_read_only(org)
        audit.record("subscription.lapse", organization=org, obj=sub)
        return "lapsed"
    return None


def _renewal_target(sub: Subscription) -> tuple[Plan | None, str]:
    """The plan and interval the next period is for: the scheduled one while it still holds what the
    organization uses, else the current one. None means "stop renewing" (a scheduled move to Free)."""
    nxt = sub.next_plan
    if nxt is not None and not entitlements.over_limits(sub.organization, nxt):
        if nxt.is_free:
            return None, Interval.MONTHLY
        return nxt, sub.next_interval or Interval.MONTHLY
    # A one-off period that has run its years carries on at the yearly price.
    return sub.plan, Interval.YEARLY if sub.interval == Interval.ONCE else sub.interval


def _stop_renewing(sub: Subscription, free: Plan) -> str:
    before = sub.plan.name
    sub.plan, sub.interval, sub.status = free, Interval.MONTHLY, Status.ACTIVE
    sub.period_start = sub.period_end = sub.past_due_since = sub.grace_until = None
    sub.next_plan, sub.next_interval = None, ""
    sub.save()
    _void_open(sub, _("Not renewed"))
    audit.record("subscription.not_renewed", organization=sub.organization, obj=sub,
                 changes={"plan": [before, free.name]})
    return "renewals"


def _grace_over(sub: Subscription, today: datetime.date) -> bool:
    """Whether the grace is used up: the policy's days, any extension a Platform Admin gave, and no open
    dispute on an invoice."""
    if sub.grace_until and sub.grace_until >= today:
        return False
    if sub.invoices.filter(status=SubscriptionInvoice.Status.OPEN, disputed_at__isnull=False).exists():
        return False
    return (today - sub.past_due_since).days >= grace_days()


# ---------------------------------------------------------------------------
# Reminders and Platform Admin tools for disputes (D-165)
# ---------------------------------------------------------------------------


# The saved card is charged this many days before the paid period ends, so a refusal leaves time to fix it.
AUTO_CHARGE_DAYS_BEFORE = 2


@transaction.atomic
def set_auto_renew(actor: Membership, on: bool, request=None) -> None:
    require(actor, "subscription.manage")
    sub = Subscription.objects.select_for_update().get(organization=actor.organization)
    if on and not sub.has_card:
        raise ValidationError(_("Pay an invoice by card and tick “Renew automatically” first."))
    sub.auto_renew = on
    sub.save(update_fields=["auto_renew", "updated_at"])
    audit.record("subscription.auto_renew", actor=actor.user, organization=actor.organization, obj=sub,
                 request=request, changes={"auto_renew": on})


@transaction.atomic
def remove_card(actor: Membership, request=None) -> None:
    """Forgets the saved card; automatic renewal goes off with it."""
    require(actor, "subscription.manage")
    sub = Subscription.objects.select_for_update().get(organization=actor.organization)
    sub.card_code_encrypted = sub.card_last4 = sub.card_brand = sub.card_expiry = ""
    sub.auto_renew = False
    sub.save(update_fields=["card_code_encrypted", "card_last4", "card_brand", "card_expiry", "auto_renew",
                            "updated_at"])
    audit.record("subscription.card_removed", actor=actor.user, organization=actor.organization, obj=sub,
                 request=request)


def billing_contacts(org: Organization) -> list[str]:
    if org.billing_email:
        return [org.billing_email]
    return sorted({m.user.email for m in Membership.objects.filter(organization=org, is_active=True,
                                                                  user__is_active=True).select_related("user", "role")
                   if m.user.email and can(m, "subscription.manage")})


def _reminder_text(sub: Subscription, kind: str, days: int) -> tuple[str, str]:
    if kind == "TRIAL":
        return (_("Your NyumbaPMS trial ends in %(n)s days") % {"n": days},
                _("Your free trial ends on %(d)s. Choose a plan on the Subscription page to keep going. Your "
                  "records stay yours either way.") % {"d": sub.trial_ends_on})
    if kind == "RENEWAL" and sub.auto_renew and sub.has_card:
        charge_on = sub.period_end - datetime.timedelta(days=AUTO_CHARGE_DAYS_BEFORE)
        return (_("Your NyumbaPMS %(plan)s plan renews in %(n)s days") % {"plan": sub.plan.name, "n": days},
                _("Your paid period ends on %(d)s. Your card (%(card)s) will be charged automatically from %(c)s "
                  "for the renewal; you do not need to do anything. To change the card or stop automatic renewal, "
                  "open the Subscription page.") % {
                    "d": sub.period_end, "c": max(charge_on, timezone.localdate()),
                    "card": f"{sub.card_brand} ending {sub.card_last4}".strip()})
    if kind == "RENEWAL":
        return (_("Your NyumbaPMS %(plan)s plan renews in %(n)s days") % {"plan": sub.plan.name, "n": days},
                _("Your paid period ends on %(d)s. The renewal invoice is on the Subscription page; paying it "
                  "keeps your plan going without a break.") % {"d": sub.period_end})
    return (_("Your NyumbaPMS payment is overdue"),
            _("Your subscription payment fell due %(n)s days ago. %(left)s days remain before the account "
              "becomes read-only: you could still see and download everything, but not add or change records. "
              "Pay on the Subscription page, or reply if you are disputing the invoice.")
            % {"n": days, "left": max(grace_days() - days, 0)})


def send_reminders(today: datetime.date | None = None) -> int:
    """Emails each organization's billing contact on the days set in BillingPolicy, once per reminder.
    Returns how many went out."""
    from django.core.mail import send_mail

    from notifications.providers.mail import SYSTEM, mailer_for

    today = today or timezone.localdate()
    pol, sent = policy(), 0
    for sub in Subscription.objects.select_related("organization", "plan", "next_plan"):
        due = None
        if sub.status == Status.TRIAL and sub.trial_ends_on and sub.trial_ends_on >= today:
            due = ("TRIAL", sub.trial_ends_on, (sub.trial_ends_on - today).days, pol.reminder_list)
        elif sub.status == Status.ACTIVE and sub.period_end and not sub.plan.is_free and sub.period_end >= today:
            if _renewal_target(sub)[0] is not None:
                due = ("RENEWAL", sub.period_end, (sub.period_end - today).days, pol.reminder_list)
        elif sub.status == Status.PAST_DUE and sub.past_due_since:
            due = ("OVERDUE", sub.past_due_since, (today - sub.past_due_since).days, pol.grace_reminder_list)
        if due is None or due[2] not in due[3]:
            continue
        kind, for_date, days, _wanted = due
        if SubscriptionReminder.objects.filter(subscription=sub, kind=kind, for_date=for_date, days=days).exists():
            continue
        to = billing_contacts(sub.organization)
        if not to:
            continue
        subject, body = _reminder_text(sub, kind, days)
        try:
            send_mail(f"[NyumbaPMS] {subject}", body, None, to, using=mailer_for(SYSTEM))
        except Exception:
            logger.exception("Subscription reminder for organization %s failed", sub.organization_id)
            continue
        SubscriptionReminder.objects.create(subscription=sub, kind=kind, for_date=for_date, days=days)
        sent += 1
    return sent


@transaction.atomic
def extend_grace(admin, sub: Subscription, days: int, reason: str, request=None) -> datetime.date:
    """Holds off the lapse for `days` more days, counted from today or from the current extension if later."""
    _require_platform_admin(admin)
    if days < 1 or days > 90:
        raise ValidationError(_("Choose between 1 and 90 days."))
    if not (reason or "").strip():
        raise ValidationError(_("Give a reason."))
    sub = Subscription.objects.select_for_update().get(pk=sub.pk)
    start = max(timezone.localdate(), sub.grace_until or timezone.localdate())
    sub.grace_until = start + datetime.timedelta(days=days)
    sub.save(update_fields=["grace_until", "updated_at"])
    audit.record("subscription.grace_extended", actor=admin, organization=sub.organization, obj=sub, request=request,
                 changes={"grace_until": str(sub.grace_until), "reason": reason.strip()})
    return sub.grace_until


@transaction.atomic
def set_dispute(admin, invoice: SubscriptionInvoice, note: str = "", *, resolved: bool = False, request=None) -> None:
    """Marks an open invoice as disputed (the account is not lapsed while it is), or clears that."""
    _require_platform_admin(admin)
    invoice = SubscriptionInvoice.objects.select_for_update().get(pk=invoice.pk)
    if resolved:
        invoice.disputed_at = None
    else:
        if invoice.status != SubscriptionInvoice.Status.OPEN:
            raise ValidationError(_("Only an open invoice can be disputed."))
        invoice.disputed_at = timezone.now()
        invoice.dispute_note = (note or invoice.dispute_note or "Marked disputed").strip()[:300]
    invoice.save(update_fields=["disputed_at", "dispute_note", "updated_at"])
    audit.record("subscription_invoice.dispute_cleared" if resolved else "subscription_invoice.disputed",
                 actor=admin, organization=invoice.organization, obj=invoice, request=request,
                 changes={"note": invoice.dispute_note})


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
        left = grace_days() - (today - (sub.past_due_since or today)).days
        if sub.grace_until and sub.grace_until >= today:
            left = max(left, (sub.grace_until - today).days)
        return {"level": "warning", "text": _("Your subscription payment is due. In %(n)s days the account "
                                              "becomes read-only; you will still see and download everything.")
                % {"n": max(left, 0)}}
    if sub.status == Status.LAPSED:
        return {"level": "danger", "text": _("Your subscription has lapsed. Your records are safe and can be "
                                             "viewed and downloaded; pay to start creating again.")}
    if sub.status == Status.ACTIVE and sub.period_end and not sub.plan.is_free:
        days = (sub.period_end - today).days
        if 0 <= days < policy().invoice_lead_days:
            change = scheduled_change(sub)
            if change and change["stops"]:
                text = _("Your plan ends in %(n)s days and will not renew.") % {"n": days}
            elif change:
                text = _("Your plan ends in %(n)s days and you move to %(plan)s.") % {"n": days,
                                                                                    "plan": change["plan"].name}
            else:
                text = _("Your plan renews in %(n)s days. The renewal invoice is ready to pay.") % {"n": days}
            return {"level": "info", "text": text}
    return None


def best_yearly_saving(plans) -> int:
    """The biggest "save X%" for paying yearly across these plans (each with `offer_list`), for the billing toggle."""
    return max((saved for plan in plans for interval, _price, saved in plan.offer_list if interval == "YEARLY"),
               default=0)
