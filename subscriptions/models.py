"""Platform billing: the landlord pays NyumbaPMS (doc 11 §22 flow A, D-060).

Nothing here links to rent invoices, payments or ledgers, and nothing in those apps links here.
Other apps ask `subscriptions.entitlements`, never these models, what a plan allows.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import PublicIdModel, TimeStampedModel


# One-off plans are no longer offered (D-146). The prices stay in the data and subscriptions already on one carry on;
# set this to True to offer them again.
ONE_OFF_OFFERED = False


class Plan(TimeStampedModel):
    key = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=60)
    # Null means no limit.
    unit_limit = models.PositiveIntegerField(null=True, blank=True)
    seat_limit = models.PositiveIntegerField(null=True, blank=True)
    # Before VAT (D-060 item 4).
    monthly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    yearly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    # Pay once for `one_off_years` of use (D-132); zero means the plan has no one-off price. After the years
    # it renews at the yearly price.
    one_off_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    one_off_years = models.PositiveSmallIntegerField(default=5)
    # A short tag on the plan's card, such as "Most popular" or "Best value"; empty for none.
    badge = models.CharField(max_length=30, blank=True)
    # What the card lists under "Included", one per line. Empty means the standard list of product features.
    features = models.TextField(blank=True, help_text=_("One feature per line, shown on this plan's card. "
                                                       "Leave empty to show the standard list."))
    # Shown on the subscription page for owners to choose; Enterprise is set up by a Platform Admin.
    self_serve = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "pk"]

    def __str__(self):
        return self.name

    @property
    def is_free(self) -> bool:
        return not self.monthly_price and not self.yearly_price and not self.one_off_price

    @property
    def has_one_off(self) -> bool:
        return ONE_OFF_OFFERED and self.one_off_price > 0 and self.one_off_years > 0

    @property
    def feature_lines(self) -> list[str]:
        """The lines typed in admin for this plan's card; empty means the card shows the standard list."""
        return [line.strip() for line in self.features.splitlines() if line.strip()]

    @property
    def yearly_per_month(self) -> Decimal:
        return (self.yearly_price / 12).quantize(Decimal("1"))

    def price(self, interval: str) -> Decimal:
        if interval == Subscription.Interval.ONCE:
            return self.one_off_price
        return self.yearly_price if interval == Subscription.Interval.YEARLY else self.monthly_price

    def offers(self) -> list[tuple[str, Decimal, int]]:
        """What the page shows: (interval, price, percent saved against paying monthly for the same time)."""
        out = []
        for interval, months in (("MONTHLY", 1), ("YEARLY", 12), ("ONCE", 12 * self.one_off_years)):
            price = self.price(interval)
            if price <= 0 or (interval == "ONCE" and not self.has_one_off):
                continue
            saved = 0 if interval == "MONTHLY" or not self.monthly_price else int(
                max(Decimal("0"), 1 - price / (self.monthly_price * months)) * 100)
            out.append((interval, price, saved))
        return out


class Subscription(TimeStampedModel):
    class Status(models.TextChoices):
        TRIAL = "TRIAL", _("Trial")
        ACTIVE = "ACTIVE", _("Active")
        PAST_DUE = "PAST_DUE", _("Payment due")
        LAPSED = "LAPSED", _("Lapsed")

    class Interval(models.TextChoices):
        MONTHLY = "MONTHLY", _("Monthly")
        YEARLY = "YEARLY", _("Yearly")
        ONCE = "ONCE", _("One-off")

    organization = models.OneToOneField("accounts.Organization", on_delete=models.PROTECT,
                                        related_name="subscription")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.TRIAL, db_index=True)
    interval = models.CharField(max_length=10, choices=Interval.choices, default=Interval.MONTHLY)
    trial_ends_on = models.DateField(null=True, blank=True)
    # The paid period; empty on a trial and on Free.
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    # When PAST_DUE started, for the 14 days of grace.
    past_due_since = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.organization} · {self.plan} · {self.get_status_display()}"


class SubscriptionInvoice(PublicIdModel, TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "OPEN", _("Open")
        PAID = "PAID", _("Paid")
        VOID = "VOID", _("Void")

    class Reason(models.TextChoices):
        NEW_PLAN = "NEW_PLAN", _("Plan chosen")
        RENEWAL = "RENEWAL", _("Renewal")
        TRIAL_END = "TRIAL_END", _("Trial ended")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT,
                                     related_name="subscription_invoices")
    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="invoices")
    number = models.CharField(max_length=30, unique=True)
    reason = models.CharField(max_length=10, choices=Reason.choices)
    status = models.CharField(max_length=5, choices=Status.choices, default=Status.OPEN, db_index=True)
    # What is being bought, snapshotted.
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="+")
    plan_name = models.CharField(max_length=60)
    interval = models.CharField(max_length=10, choices=Subscription.Interval.choices)
    # A renewal names its period; a new plan's period starts on the payment date.
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    # The customer, snapshotted (doc 11 §22 rule 6).
    customer_name = models.CharField(max_length=150)
    customer_kra_pin = models.CharField(max_length=20, blank=True)
    customer_email = models.EmailField(blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    # Unused days of the previous paid period (D-060 item 5), a positive amount taken off.
    credit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    net_amount = models.DecimalField(max_digits=12, decimal_places=2)
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    total = models.DecimalField(max_digits=12, decimal_places=2)
    issued_on = models.DateField()
    due_on = models.DateField()
    paid_at = models.DateTimeField(null=True, blank=True)
    # Filled by a real eTIMS adapter (D-060 item 11).
    etims_reference = models.CharField(max_length=100, blank=True)
    void_reason = models.CharField(max_length=200, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    class Meta:
        ordering = ["-issued_on", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(total__gte=0), name="subscriptions_invoice_total_not_negative"),
        ]

    def __str__(self):
        return self.number

    @property
    def paid(self) -> Decimal:
        return sum((p.amount for p in self.payments.all()), Decimal("0"))

    @property
    def outstanding(self) -> Decimal:
        return max(self.total - self.paid, Decimal("0")) if self.status == self.Status.OPEN else Decimal("0")


class SubscriptionPayment(PublicIdModel):
    """Money received by NyumbaPMS, for an invoice or an SMS top-up. Never deleted."""

    class Method(models.TextChoices):
        MPESA = "MPESA", _("M-Pesa")
        PAYSTACK = "PAYSTACK", _("Paystack")
        BANK = "BANK", _("Bank transfer")
        OTHER = "OTHER", _("Other")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT,
                                     related_name="subscription_payments")
    invoice = models.ForeignKey(SubscriptionInvoice, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=10, choices=Method.choices)
    reference = models.CharField(max_length=60, unique=True)
    paid_on = models.DateField()
    note = models.CharField(max_length=200, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-paid_on", "-pk"]
        constraints = [models.CheckConstraint(condition=Q(amount__gt=0), name="subscriptions_payment_positive")]

    def __str__(self):
        return f"{self.reference} {self.amount}"


class PlatformReceipt(PublicIdModel):
    payment = models.OneToOneField(SubscriptionPayment, on_delete=models.PROTECT, related_name="receipt")
    number = models.CharField(max_length=30, unique=True)
    issued_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.number


class PlatformSequence(models.Model):
    """Our own document numbers (LPM-INV-2026-000001). Not per organization, unlike core.NumberSequence."""

    key = models.CharField(max_length=30)
    period = models.CharField(max_length=10, blank=True)
    next_value = models.PositiveBigIntegerField(default=1)

    class Meta:
        constraints = [models.UniqueConstraint("key", "period", name="subscriptions_platformsequence_unique")]

    def __str__(self):
        return f"{self.key} {self.period}: {self.next_value}"


class SmsWallet(models.Model):
    """The balance, kept on one row so it can be locked; the entries are the record. The mode says who pays
    for the organization's SMS and the monthly figure what it gets (D-142)."""

    class Mode(models.TextChoices):
        SPONSORED = "SPONSORED", _("Sponsored: we pay, up to the monthly cap")
        PREPAID = "PREPAID", _("Prepaid: the organization pays from its wallet")
        BUNDLED = "BUNDLED", _("Bundled: a monthly allowance, then the wallet")

    organization = models.OneToOneField("accounts.Organization", on_delete=models.PROTECT, related_name="sms_wallet")
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.SPONSORED)
    # SMS parts a month. Sponsored: the most we pay for (empty means no cap). Bundled: the parts included before
    # the wallet is charged. Prepaid: not used.
    monthly_cap = models.PositiveIntegerField(_("monthly parts"), null=True, blank=True, default=300,
                                              help_text=_("Sponsored: the most SMS parts we pay for in a month "
                                                          "(empty for no cap). Bundled: parts included each month."))
    # The warning last sent this month (services.ALERT_*), so each is sent once; reset on a new month, a top-up
    # and a change of terms.
    alert_month = models.DateField(null=True, blank=True)
    alert_level = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return f"{self.organization} {self.balance}"


class SmsWalletEntry(models.Model):
    class Kind(models.TextChoices):
        TOP_UP = "TOP_UP", _("Top-up")
        MESSAGE = "MESSAGE", _("Message")
        ADJUSTMENT = "ADJUSTMENT", _("Adjustment")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    # Signed: a top-up adds, a message takes away.
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    # SMS parts a message used, whoever paid for them; zero for money entries.
    segments = models.PositiveIntegerField(default=0)
    payment = models.OneToOneField(SubscriptionPayment, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="sms_top_up")
    message = models.OneToOneField("notifications.Message", on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    note = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name_plural = "SMS wallet entries"

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount}"


# ---------------------------------------------------------------------------
# Landlords paying online (D-126): our own Paybill through Daraja, and Paystack
# ---------------------------------------------------------------------------


class PlatformPaySwitch(models.Model):
    """Which ways of paying online the subscription page offers. One row, changed in Django admin.

    The keys stay in the environment; a way is offered only when its keys are set and it is on here,
    so either can be turned off for a while without touching the server."""

    mpesa_on = models.BooleanField(_("M-Pesa prompt and Paybill (Daraja)"), default=True)
    paystack_on = models.BooleanField(_("Paystack (card or M-Pesa)"), default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "online payment switch"
        verbose_name_plural = "online payment switch"

    def __str__(self):
        return "Online payments"

    @classmethod
    def get(cls) -> "PlatformPaySwitch":
        return cls.objects.get_or_create(pk=1)[0]


class PlatformCheckout(PublicIdModel):
    """One attempt to pay an invoice online: an M-Pesa prompt to the landlord's phone, or a Paystack
    checkout page. The money itself arrives as a PlatformTransaction."""

    class Provider(models.TextChoices):
        MPESA = "MPESA", _("M-Pesa prompt")
        PAYSTACK = "PAYSTACK", _("Paystack")

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Waiting")
        PAID = "PAID", _("Paid")
        FAILED = "FAILED", _("Not paid")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    # Empty for an SMS top-up (D-142): the money goes to the organization's SMS wallet.
    invoice = models.ForeignKey(SubscriptionInvoice, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="checkouts")
    provider = models.CharField(max_length=10, choices=Provider.choices)
    # Daraja's CheckoutRequestID, or our Paystack reference.
    reference = models.CharField(max_length=80, unique=True)
    phone = models.CharField(max_length=20, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    result = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.get_provider_display()} {self.invoice or 'SMS credit'} {self.get_status_display()}"


class PlatformTransaction(models.Model):
    """Money Safaricom or Paystack told us about, kept once per code exactly as received. APPLIED ones
    became a SubscriptionPayment; UNMATCHED ones wait for a Platform Admin, who records the payment by
    hand with the same code (that links them)."""

    class Source(models.TextChoices):
        STK = "STK", _("M-Pesa prompt")
        C2B = "C2B", _("Paybill")
        PAYSTACK = "PAYSTACK", _("Paystack")

    class Status(models.TextChoices):
        APPLIED = "APPLIED", _("Applied")
        UNMATCHED = "UNMATCHED", _("Needs a person")

    source = models.CharField(max_length=10, choices=Source.choices)
    # The M-Pesa receipt, or PS<Paystack id>.
    code = models.CharField(max_length=30, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="KES")
    account = models.CharField(_("account typed"), max_length=60, blank=True)
    phone = models.CharField(max_length=80, blank=True)
    payer_name = models.CharField(max_length=150, blank=True)
    paid_at = models.DateTimeField()
    invoice = models.ForeignKey(SubscriptionInvoice, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="+")
    payment = models.OneToOneField(SubscriptionPayment, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="platform_transaction")
    status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
    note = models.CharField(max_length=300, blank=True)
    raw_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.code} {self.amount}"
