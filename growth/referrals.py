"""Refer a landlord (D-133). Both sides get free days once the new organization has paid us for the first time.

Nothing is paid for sign-ups alone, so a code handed out in bulk to fake accounts earns nothing."""

import datetime
import secrets

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit import services as audit

from . import campaigns
from .models import Referral, ReferralCode, SignupSource

# No 0/O or 1/I/L, which are mistaken for each other when a code is read out over the phone.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def reward_days() -> int:
    return int(getattr(settings, "REFERRAL_REWARD_DAYS", 30))


def yearly_cap() -> int:
    return int(getattr(settings, "REFERRAL_MAX_REWARDS_PER_YEAR", 12))


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
    if tags.get("source") is not None or tags.get("referrer_host"):
        SignupSource.objects.get_or_create(organization=org, defaults={
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


def on_payment(org) -> None:
    """Called when an organization's subscription invoice is paid in full. Rewards the first time only."""
    days = reward_days()
    referral = Referral.objects.select_for_update().filter(referred=org, status=Referral.Status.SIGNED_UP).first()
    if referral is None or days <= 0:
        return
    since = timezone.now() - datetime.timedelta(days=365)
    given = Referral.objects.filter(referrer=referral.referrer, status=Referral.Status.REWARDED,
                                    rewarded_at__gte=since).count()
    referral.rewarded_at = timezone.now()
    if given >= yearly_cap():
        referral.status = Referral.Status.REFERRER_LAPSED
        referral.save(update_fields=["status", "rewarded_at", "updated_at"])
        return
    _extend(org, days)  # the new customer: just paid, so ACTIVE with a period
    if _extend(referral.referrer, days):
        referral.status, referral.reward_days = Referral.Status.REWARDED, days
    else:
        referral.status = Referral.Status.REFERRER_LAPSED
    referral.save(update_fields=["status", "reward_days", "rewarded_at", "updated_at"])
    audit.record("referral.reward", actor=None, organization=referral.referrer, obj=referral,
                 changes={"status": [Referral.Status.SIGNED_UP, referral.status], "days": days})


def summary(org) -> dict:
    rows = list(Referral.objects.filter(referrer=org))
    earned = sum(r.reward_days for r in rows)
    link = link_for(org)
    text = f"I use NyumbaPMS to collect rent and track my tenants. Try it free, and we both get free days: {link}"
    return {"code": code_for(org), "link": link, "referrals": rows, "days_earned": earned, "share_text": text,
            "days_each": reward_days(), "yearly_cap": yearly_cap(),
            "paying": sum(r.status == Referral.Status.REWARDED for r in rows)}
