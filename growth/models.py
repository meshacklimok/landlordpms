"""Demo requests, referrals and where sign-ups came from (D-133)."""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import PublicIdModel, TimeStampedModel


class DemoRequest(PublicIdModel, TimeStampedModel):
    """A visitor asking for a demo from the public form. Worked by a Platform Admin in Django admin."""

    class Status(models.TextChoices):
        NEW = "NEW", _("New")
        CONTACTED = "CONTACTED", _("Contacted")
        BOOKED = "BOOKED", _("Booked")
        DONE = "DONE", _("Demo given")
        CANCELLED = "CANCELLED", _("Cancelled")

    class Size(models.TextChoices):
        S = "1-10", _("1 to 10 units")
        M = "11-30", _("11 to 30 units")
        L = "31-100", _("31 to 100 units")
        XL = "100+", _("More than 100 units")

    class Kind(models.TextChoices):
        LANDLORD = "LANDLORD", _("I own and manage my own properties")
        AGENCY = "AGENCY", _("I manage properties for other owners")
        ESTATE = "ESTATE", _("A gated community, estate or residents' association")
        WATER = "WATER", _("A water or garbage service")
        OTHER = "OTHER", _("Something else")

    class Channel(models.TextChoices):
        CALL = "CALL", _("A phone or WhatsApp call")
        VIDEO = "VIDEO", _("A video call")
        VISIT = "VISIT", _("Visit me at my property or office")

    class Slot(models.TextChoices):
        MORNING = "MORNING", _("Morning (9 to 12)")
        AFTERNOON = "AFTERNOON", _("Afternoon (12 to 5)")
        EVENING = "EVENING", _("Evening (after 5)")

    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    organization_name = models.CharField(max_length=150, blank=True)
    size = models.CharField(max_length=8, choices=Size.choices)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.LANDLORD)
    channel = models.CharField(max_length=6, choices=Channel.choices, default=Channel.CALL)
    preferred_date = models.DateField()
    preferred_slot = models.CharField(max_length=10, choices=Slot.choices, default=Slot.MORNING)
    message = models.TextField(max_length=1000, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW, db_index=True)
    internal_note = models.TextField(max_length=1000, blank=True)
    # Where the visitor came from, from the campaign captured in the session (see growth.campaigns).
    source = models.CharField(max_length=60, blank=True)
    campaign = models.CharField(max_length=60, blank=True)
    # Ad click ids, for importing conversions back into Google Ads or Meta (D-167).
    gclid = models.CharField(max_length=200, blank=True)
    fbclid = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} ({self.get_size_display()})"


class ReferralCode(TimeStampedModel):
    """The code an organization shares. One each, made the first time the Refer page is opened."""

    organization = models.OneToOneField("accounts.Organization", on_delete=models.PROTECT,
                                        related_name="referral_code")
    code = models.CharField(max_length=12, unique=True)

    def __str__(self):
        return self.code


class Referral(TimeStampedModel):
    """One organization that signed up with another's code. Rewarded once, when the new one first pays us."""

    class Status(models.TextChoices):
        SIGNED_UP = "SIGNED_UP", _("Signed up")
        REWARDED = "REWARDED", _("Rewarded")
        # Paid, but the referrer could not be given the days (their subscription had lapsed).
        REFERRER_LAPSED = "REFERRER_LAPSED", _("Paid, referrer not active")

    referrer = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="referrals_made")
    referred = models.OneToOneField("accounts.Organization", on_delete=models.PROTECT,
                                    related_name="referred_by")
    code = models.CharField(max_length=12)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SIGNED_UP, db_index=True)
    reward_days = models.PositiveSmallIntegerField(default=0)
    reward_percent = models.PositiveSmallIntegerField(default=0)
    rewarded_at = models.DateTimeField(null=True, blank=True)
    # The invited organization is rewarded when it has set up, without waiting for the referrer to have paid.
    referred_rewarded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.referrer} -> {self.referred}"


class ReferralPolicy(models.Model):
    """What a referral earns and when it counts. One row, edited by a Platform Admin."""

    class RewardKind(models.TextChoices):
        DISCOUNT = "DISCOUNT", _("Percent off the next plan invoice")
        DAYS = "DAYS", _("Free days added to the plan")

    reward_kind = models.CharField(_("referrer's reward"), max_length=10, choices=RewardKind.choices,
                                   default=RewardKind.DISCOUNT)
    discount_percent = models.PositiveSmallIntegerField(
        _("referrer's discount percent"), default=50, validators=[MinValueValidator(1), MaxValueValidator(90)],
        help_text=_("Taken off the referrer's next plan invoice (one invoice per referral). Used when the "
                    "referrer's reward is a discount."))
    reward_days = models.PositiveSmallIntegerField(_("referrer's free days"), default=30,
                                                   help_text=_("Used when the referrer's reward is free days."))
    reward_referred_too = models.BooleanField(
        _("reward the invited landlord too"), default=True,
        help_text=_("Off: only the referrer is rewarded; the invited landlord keeps the normal trial."))
    referred_reward_kind = models.CharField(_("invited landlord's reward"), max_length=10, choices=RewardKind.choices,
                                            default=RewardKind.DAYS)
    referred_discount_percent = models.PositiveSmallIntegerField(
        _("invited landlord's discount percent"), default=50,
        validators=[MinValueValidator(1), MaxValueValidator(90)],
        help_text=_("Used when the invited landlord's reward is a discount."))
    referred_reward_days = models.PositiveSmallIntegerField(
        _("invited landlord's free days"), default=30,
        help_text=_("Added to their trial or paid period. Used when their reward is free days."))
    require_referrer_paid = models.BooleanField(
        _("referrer must have paid first"), default=True,
        help_text=_("On: the referrer is rewarded only once they have paid us at least once; a referral waiting on "
                    "this is rewarded when they pay. The invited landlord is not kept waiting."))
    require_payment = models.BooleanField(
        _("invited landlord must have paid first"), default=False,
        help_text=_("Untick to reward as soon as they have set up, before their first payment."))
    max_rewards_per_year = models.PositiveSmallIntegerField(_("rewards per referrer a year"), default=12)
    min_properties = models.PositiveSmallIntegerField(_("properties added"), default=1)
    min_units = models.PositiveSmallIntegerField(_("units added"), default=11)
    min_tenants = models.PositiveSmallIntegerField(_("tenants added"), default=11)
    min_leases = models.PositiveSmallIntegerField(_("leases created"), default=0)
    min_invoices = models.PositiveSmallIntegerField(_("rent invoices issued"), default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("referral policy")
        verbose_name_plural = _("referral policy")

    def __str__(self):
        return "Referral policy"

    @classmethod
    def get(cls) -> "ReferralPolicy":
        return cls.objects.get_or_create(pk=1)[0]

    @property
    def is_discount(self) -> bool:
        return self.reward_kind == self.RewardKind.DISCOUNT

    @property
    def referred_is_discount(self) -> bool:
        return self.referred_reward_kind == self.RewardKind.DISCOUNT


class ReferralCredit(TimeStampedModel):
    """A percent off one plan invoice, earned by a referral and used on the organization's next invoice."""

    referral = models.ForeignKey(Referral, on_delete=models.PROTECT, related_name="credits")
    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT,
                                     related_name="referral_credits")
    percent = models.PositiveSmallIntegerField()
    invoice = models.ForeignKey("subscriptions.SubscriptionInvoice", on_delete=models.SET_NULL, null=True,
                                blank=True, related_name="referral_credits")
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.percent}% for {self.organization}"


class SignupSource(TimeStampedModel):
    """How an organization first found us: the campaign tags on the link, or the site that sent them."""

    organization = models.OneToOneField("accounts.Organization", on_delete=models.PROTECT,
                                        related_name="signup_source")
    source = models.CharField(max_length=60, blank=True)
    medium = models.CharField(max_length=60, blank=True)
    campaign = models.CharField(max_length=60, blank=True)
    referrer_host = models.CharField(max_length=100, blank=True)
    landing_path = models.CharField(max_length=100, blank=True)
    gclid = models.CharField(max_length=200, blank=True)
    fbclid = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.organization}: {self.source or self.referrer_host or 'direct'}"


