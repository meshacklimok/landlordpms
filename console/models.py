from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import TimeStampedModel


class PlatformStaff(TimeStampedModel):
    """A person who works on the platform itself (D-135): the developer's own team, not a landlord's staff.

    The role is a starting set of what they may see; `capabilities` is what they may actually see, so the owner can
    take one away or add one for a single person. Superusers need no row: they are owners.
    """

    class Role(models.TextChoices):
        OWNER = "OWNER", _("Owner")
        ANALYST = "ANALYST", _("Analyst")
        FINANCE = "FINANCE", _("Finance")
        SUPPORT = "SUPPORT", _("Support")
        ENGINEER = "ENGINEER", _("Engineer")

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="platform_seat")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.ANALYST)
    capabilities = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True, editable=False)
    note = models.CharField(max_length=200, blank=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="+")

    class Meta:
        ordering = ["user__full_name"]

    def __str__(self):
        return f"{self.user} · {self.get_role_display()}"


class PlatformAnnouncement(TimeStampedModel):
    """A notice shown at the top of every signed-in page until it ends or is switched off (D-137)."""

    class Level(models.TextChoices):
        INFO = "info", _("Information")
        WARNING = "warning", _("Warning")
        DANGER = "danger", _("Urgent")

    message = models.CharField(max_length=300)
    level = models.CharField(max_length=8, choices=Level.choices, default=Level.INFO)
    is_active = models.BooleanField(default=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.message[:60]


class PlatformExpense(TimeStampedModel):
    """What running NyumbaPMS costs the developer: hosting, AI, messaging and so on (D-138).

    A one-off entry counts in the month it was paid. A monthly or yearly one counts in every month it applies to,
    until `ends_on`, so a subscription is entered once."""

    class Category(models.TextChoices):
        HOSTING = "HOSTING", _("Hosting and servers")
        AI = "AI", _("AI (OpenAI)")
        EMAIL = "EMAIL", _("Email sending")
        SMS = "SMS", _("SMS")
        WHATSAPP = "WHATSAPP", _("WhatsApp")
        SOFTWARE = "SOFTWARE", _("Software and subscriptions")
        DOMAIN = "DOMAIN", _("Domain and certificates")
        PAYMENT_FEES = "PAYMENT_FEES", _("Payment fees (M-Pesa, Paystack)")
        STAFF = "STAFF", _("People and contractors")
        MARKETING = "MARKETING", _("Marketing")
        LEGAL = "LEGAL", _("Legal and compliance")
        OTHER = "OTHER", _("Other")

    class Repeat(models.TextChoices):
        ONCE = "ONCE", _("Once")
        MONTHLY = "MONTHLY", _("Every month")
        YEARLY = "YEARLY", _("Every year")

    spent_on = models.DateField(_("date paid"))
    category = models.CharField(_("category"), max_length=14, choices=Category.choices)
    vendor = models.CharField(_("paid to"), max_length=100, blank=True)
    amount = models.DecimalField(_("amount (KES)"), max_digits=12, decimal_places=2)
    repeat = models.CharField(_("repeats"), max_length=8, choices=Repeat.choices, default=Repeat.ONCE)
    ends_on = models.DateField(_("stops after"), null=True, blank=True)
    note = models.CharField(_("note"), max_length=300, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")

    class Meta:
        ordering = ["-spent_on", "-pk"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="analytics_expense_positive")]

    def __str__(self):
        return f"{self.get_category_display()} {self.amount}"

    def applies_in(self, month) -> bool:
        """Whether this cost falls in the calendar month starting `month`."""
        start = self.spent_on.replace(day=1)
        if month < start:
            return False
        if self.ends_on and month > self.ends_on.replace(day=1):
            return False
        if self.repeat == self.Repeat.ONCE:
            return month == start
        if self.repeat == self.Repeat.MONTHLY:
            return True
        return month.month == start.month


class OwnerEmail(TimeStampedModel):
    """One email the platform owner sent to a group of organization owners (D-179): the record of what went out."""

    subject = models.CharField(max_length=200)
    body = models.TextField()
    audience = models.CharField(max_length=300, blank=True, help_text="The filters or the picks, in words.")
    recipients = models.PositiveIntegerField(default=0)
    sent = models.PositiveIntegerField(default=0)
    failed = models.PositiveIntegerField(default=0)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.subject


class OwnerEmailRecipient(models.Model):
    email_log = models.ForeignKey(OwnerEmail, on_delete=models.CASCADE, related_name="deliveries")
    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    address = models.EmailField()
    ok = models.BooleanField(default=False)
    error = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["organization__name"]

    def __str__(self):
        return self.address
