"""Platform billing: the landlord pays LandlordPMS (doc 11 §22 flow A, D-060).

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


class Plan(TimeStampedModel):
    key = models.SlugField(max_length=30, unique=True)
    name = models.CharField(max_length=60)
    # Null means no limit.
    unit_limit = models.PositiveIntegerField(null=True, blank=True)
    seat_limit = models.PositiveIntegerField(null=True, blank=True)
    # Before VAT (D-060 item 4).
    monthly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    yearly_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
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
        return not self.monthly_price and not self.yearly_price

    def price(self, interval: str) -> Decimal:
        return self.yearly_price if interval == Subscription.Interval.YEARLY else self.monthly_price


class Subscription(TimeStampedModel):
    class Status(models.TextChoices):
        TRIAL = "TRIAL", _("Trial")
        ACTIVE = "ACTIVE", _("Active")
        PAST_DUE = "PAST_DUE", _("Payment due")
        LAPSED = "LAPSED", _("Lapsed")

    class Interval(models.TextChoices):
        MONTHLY = "MONTHLY", _("Monthly")
        YEARLY = "YEARLY", _("Yearly")

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
    """Money received by LandlordPMS, for an invoice or an SMS top-up. Never deleted."""

    class Method(models.TextChoices):
        MPESA = "MPESA", _("M-Pesa")
        BANK = "BANK", _("Bank transfer")
        OTHER = "OTHER", _("Other")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT,
                                     related_name="subscription_payments")
    invoice = models.ForeignKey(SubscriptionInvoice, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=5, choices=Method.choices)
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
    """The balance, kept on one row so it can be locked; the entries are the record."""

    organization = models.OneToOneField("accounts.Organization", on_delete=models.PROTECT, related_name="sms_wallet")
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))

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
