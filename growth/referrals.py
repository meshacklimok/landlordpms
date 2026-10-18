"""Refer a landlord (D-133). What a referral earns and when it counts is the ReferralPolicy a Platform Admin edits.

The referrer and the invited organization each get their own reward: a percent off the next plan invoice (a
ReferralCredit, used by subscriptions.services.issue_invoice) or free days added to the trial or paid period. It is
given once the invited organization has set up the properties, units, tenants, leases and rent invoices the policy
asks for, when the policy says so has paid us for the first time. The invited organization is rewarded as soon as
it is ready; the referrer's reward also waits, when the policy says so, until the referrer has paid us at
least once. Nothing is given for a sign-up alone, so a code handed out in bulk to empty accounts earns
nothing. The daily job (`subscriptions_daily`) checks the referrals still waiting."""

import datetime
import logging
import secrets

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from audit import services as audit

from . import campaigns
from .models import Referral, ReferralCode, ReferralCredit, ReferralPolicy, SignupSource

logger = logging.getLogger(__name__)

# No 0/O or 1/I/L, which are mistaken for each other when a code is read out over the phone.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def policy() -> ReferralPolicy:
    return ReferralPolicy.get()


def reward_days() -> int:
    return policy().reward_days


def yearly_cap() -> int:
    return policy().max_rewards_per_year


def code_for(org) -> str:
    existing = ReferralCode.objects.filter(organization=org).first()
    if existing:
        return existing.code
    for _attempt in range(10):
        code = "".join(secrets.choice(ALPHABET) for _i in range(8))
        try:
            with transaction.atomic():
                return ReferralCode.objects.create(organization=org, code=code).code
        except IntegrityError:
            if (again := ReferralCode.objects.filter(organization=org).first()):
                return again.code  # made by a second request at the same moment
    raise RuntimeError("Could not make a referral code.")


def link_for(org) -> str:
    return f"{settings.SITE_URL}/register/?ref={code_for(org)}"


def attach(org, request) -> None:
    """Records how a new organization found us and who referred it. Never blocks creating the organization."""
    tags = campaigns.seen(request)
    if not tags:
        return
    if tags.get("source") is not None or tags.get("referrer_host") or tags.get("gclid") or tags.get("fbclid"):
        SignupSource.objects.get_or_create(organization=org, defaults={
            "gclid": tags.get("gclid", ""), "fbclid": tags.get("fbclid", ""),
            "source": tags.get("source", ""), "medium": tags.get("medium", ""),
            "campaign": tags.get("campaign", ""), "referrer_host": tags.get("referrer_host", ""),
            "landing_path": tags.get("landing", "")})
    code = tags.get("ref")
    row = ReferralCode.objects.select_related("organization").filter(code=code).first() if code else None
    if row is None or row.organization_id == org.pk:
        return
    referrer = row.organization
    # The same person opening a second organization is not a new customer.
    if org.created_by_id and referrer.created_by_id == org.created_by_id:
        return
    if org.billing_phone and referrer.billing_phone == org.billing_phone:
        return
    referral, created = Referral.objects.get_or_create(referred=org, defaults={"referrer": referrer, "code": row.code})
    if created:
        audit.record("referral.signup", actor=org.created_by, organization=referrer, obj=referral,
                     request=request, changes={"code": row.code})


def _extend(org, days: int) -> bool:
    """Adds days to what the organization has paid for, or to its trial. False when it has neither."""
    from subscriptions.models import Subscription

    sub = Subscription.objects.select_for_update().filter(organization=org).first()
    if sub is None:
        return False
    delta = datetime.timedelta(days=days)
    if sub.status == Subscription.Status.TRIAL and sub.trial_ends_on:
        sub.trial_ends_on += delta
    elif sub.status in (Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE) and sub.period_end:
        sub.period_end += delta
    else:
        return False
    sub.save(update_fields=["trial_ends_on", "period_end", "updated_at"])
    return True


def setup_counts(org) -> dict[str, int]:
    """What the organization has set up, for the policy's minimums."""
    from billing.models import Invoice
    from leases.models import Lease
    from properties.models import Property, Unit
    from tenants.models import Tenant

    return {"properties": Property.objects.filter(organization=org).count(),
            "units": Unit.objects.filter(organization=org).count(),
            "tenants": Tenant.objects.filter(organization=org).count(),
            "leases": Lease.objects.filter(organization=org).count(),
            "invoices": Invoice.objects.filter(organization=org).exclude(
                status__in=(Invoice.Status.DRAFT, Invoice.Status.VOID)).count()}


def minimums(rule: ReferralPolicy) -> dict[str, int]:
    return {"properties": rule.min_properties, "units": rule.min_units, "tenants": rule.min_tenants,
            "leases": rule.min_leases, "invoices": rule.min_invoices}


def is_set_up(org, rule: ReferralPolicy) -> bool:
    have = setup_counts(org)
    return all(have[k] >= need for k, need in minimums(rule).items())


def has_paid(org) -> bool:
    from subscriptions.models import SubscriptionInvoice

    return SubscriptionInvoice.objects.filter(organization=org, status=SubscriptionInvoice.Status.PAID,
                                              total__gt=0).exists()


def _paid(org, paid_org=None) -> bool:
    """`paid_org` is an organization whose payment is being recorded right now, so it counts as paid already."""
    return org.pk == getattr(paid_org, "pk", None) or has_paid(org)


def _invited_ready(referral: Referral, rule: ReferralPolicy, paid_org=None) -> bool:
    """The invited organization has done what the policy asks of it."""
    if rule.require_payment and not _paid(referral.referred, paid_org):
        return False
    return is_set_up(referral.referred, rule)


def _give(org, referral: Referral, *, discount: bool, percent: int, days: int) -> bool:
    """One side's reward. A credit waits for the next invoice, so a plan that has lapsed still gets it on renewal."""
    if discount:
        ReferralCredit.objects.create(referral=referral, organization=org, percent=percent)
        return True
    return days > 0 and _extend(org, days)


def _reward_text(*, discount: bool, percent: int, days: int) -> str:
    """What was earned, in words, for the in-app notice."""
    if discount:
        return f"{percent}% off your next plan invoice"
    return f"{days} free days added to your plan"


def _tell(org, codename: str, referral: Referral, text: str) -> None:
    """In-app notice to those who manage the plan. A failure here must never undo the reward."""
    from notifications.delivery import notify, staff_with

    try:
        for user in staff_with(org, "subscription.manage"):
            notify(org, codename, user=user, context={"reward": text},
                   dedupe_key=f"{codename}:{referral.pk}:{user.pk}")
    except Exception:  # noqa: BLE001
        logger.exception("Could not tell %s about referral %s", org.pk, referral.pk)


def _reward_invited(referral: Referral, rule: ReferralPolicy) -> None:
    """The invited organization's reward, once, as soon as it is ready; the referrer's payment is not waited for."""
    referral.referred_rewarded_at = timezone.now()
    referral.save(update_fields=["referred_rewarded_at", "updated_at"])
    _give(referral.referred, referral, discount=rule.referred_is_discount,
          percent=rule.referred_discount_percent, days=rule.referred_reward_days)
    _tell(referral.referred, "referral_welcome", referral,
          _reward_text(discount=rule.referred_is_discount, percent=rule.referred_discount_percent,
                       days=rule.referred_reward_days))
    audit.record("referral.reward_invited", actor=None, organization=referral.referred, obj=referral,
                 changes={"percent": rule.referred_discount_percent if rule.referred_is_discount else 0,
                          "days": 0 if rule.referred_is_discount else rule.referred_reward_days})


def _reward(referral: Referral, rule: ReferralPolicy) -> None:
    """Gives the reward to the referrer."""
    since = timezone.now() - datetime.timedelta(days=365)
    given = Referral.objects.filter(referrer=referral.referrer, status=Referral.Status.REWARDED,
                                    rewarded_at__gte=since).count()
    referral.rewarded_at = timezone.now()
    if given >= rule.max_rewards_per_year:
        referral.status = Referral.Status.REFERRER_LAPSED
        referral.save(update_fields=["status", "rewarded_at", "updated_at"])
        return
    if _give(referral.referrer, referral, discount=rule.is_discount, percent=rule.discount_percent,
             days=rule.reward_days):
        referral.status = Referral.Status.REWARDED
        referral.reward_percent, referral.reward_days = ((rule.discount_percent, 0) if rule.is_discount
                                                         else (0, rule.reward_days))
    else:
        referral.status = Referral.Status.REFERRER_LAPSED
    referral.save(update_fields=["status", "reward_days", "reward_percent", "rewarded_at", "updated_at"])
    if referral.status == Referral.Status.REWARDED:
        _tell(referral.referrer, "referral_rewarded", referral,
              _reward_text(discount=rule.is_discount, percent=rule.discount_percent, days=rule.reward_days))
    audit.record("referral.reward", actor=None, organization=referral.referrer, obj=referral,
                 changes={"status": [Referral.Status.SIGNED_UP, referral.status],
                          "percent": referral.reward_percent, "days": referral.reward_days})


def _settle(referral: Referral, rule: ReferralPolicy, paid_org=None) -> None:
    """Gives whichever rewards are now due. The invited organization first; the referrer also needs to have paid
    when the policy says so, and keeps waiting (status stays "signed up") until they do."""
    if not _invited_ready(referral, rule, paid_org):
        return
    if rule.reward_referred_too and referral.referred_rewarded_at is None:
        _reward_invited(referral, rule)
    if not rule.require_referrer_paid or _paid(referral.referrer, paid_org):
        _reward(referral, rule)
    else:  # once per referral: the notice is deduplicated, and the daily job comes back to it every day
        _tell(referral.referrer, "referral_waiting", referral,
              _reward_text(discount=rule.is_discount, percent=rule.discount_percent, days=rule.reward_days))


def on_payment(org) -> None:
    """Called when an organization's subscription invoice is paid in full. It may be the invited organization's first
    payment, or the referrer's first payment for a referral that was waiting on it."""
    rule = policy()
    waiting = (Referral.objects.select_for_update(of=("self",)).select_related("referrer", "referred")
               .filter(Q(referred=org) | Q(referrer=org), status=Referral.Status.SIGNED_UP))
    for referral in waiting:
        _settle(referral, rule, paid_org=org)


def check_pending() -> int:
    """The daily job: rewards the referrals whose invited organization has now done what the policy asks."""
    rule = policy()
    done = 0
    for pk in Referral.objects.filter(status=Referral.Status.SIGNED_UP).values_list("pk", flat=True):
        with transaction.atomic():
            referral = (Referral.objects.select_for_update(of=("self",)).select_related("referrer", "referred")
                        .filter(pk=pk, status=Referral.Status.SIGNED_UP).first())
            if referral is not None:
                _settle(referral, rule)
                done += referral.status == Referral.Status.REWARDED
    return done


def take_credit(org, invoice) -> ReferralCredit | None:
    """The oldest unused credit of the organization, marked used by `invoice`. One credit per invoice."""
    credit = (ReferralCredit.objects.select_for_update().filter(organization=org, used_at__isnull=True)
              .order_by("created_at", "pk").first())
    if credit is not None:
        credit.invoice, credit.used_at = invoice, timezone.now()
        credit.save(update_fields=["invoice", "used_at", "updated_at"])
    return credit


def next_credit_percent(org) -> int:
    credit = ReferralCredit.objects.filter(organization=org, used_at__isnull=True).order_by("created_at", "pk").first()
    return credit.percent if credit else 0


def summary(org) -> dict:
    rule = policy()
    rows = list(Referral.objects.filter(referrer=org))
    link = link_for(org)
    offer = f"you get {offer_text(rule)}" if rule.reward_referred_too else "it is free to start"
    text = f"I use NyumbaPMS to collect rent and track my tenants. Try it, {offer}: {link}"
    credits = ReferralCredit.objects.filter(organization=org)
    return {"code": code_for(org), "link": link, "referrals": rows, "share_text": text, "policy": rule,
            "minimums": {k: v for k, v in minimums(rule).items() if v}, "yearly_cap": rule.max_rewards_per_year,
            "days_earned": sum(r.reward_days for r in rows),
            "credits_waiting": credits.filter(used_at__isnull=True).count(),
            "credits_used": credits.filter(used_at__isnull=False).count(),
            "referrer_must_pay": rule.require_referrer_paid and not has_paid(org),
            "rewarded": sum(r.status == Referral.Status.REWARDED for r in rows)}


def offer_text(rule: ReferralPolicy) -> str:
    """What the invited landlord is promised, in words."""
    if rule.referred_is_discount:
        return f"{rule.referred_discount_percent}% off a plan invoice"
    days = rule.referred_reward_days
    return "1 month free" if days == 30 else f"{days} free days"


def invite_offer(request) -> str:
    """What the sign-up pages tell someone who followed a referral link; empty without a valid code."""
    from django.utils.translation import gettext as _

    if not campaigns.is_valid_invite(request):
        return ""
    rule = policy()
    if not rule.reward_referred_too:
        return _("You were invited by a NyumbaPMS customer.")
    if rule.referred_is_discount:
        reward = _("%(p)s%% off a plan invoice") % {"p": rule.referred_discount_percent}
    else:
        reward = _("%(d)s free days") % {"d": rule.referred_reward_days}
    when = _("set up and paid for your first plan") if rule.require_payment else _("set up")
    return _("You were invited by a NyumbaPMS customer. Once you have %(when)s, you get %(reward)s.") % {
        "when": when, "reward": reward}
