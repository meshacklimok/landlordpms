"""Money paid out to property owners, and statements sent to them (D-058).

The statement itself stays live (D-053); a send keeps the PDF and the figures as they were sent.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import PublicIdModel, TimeStampedModel


class RemittanceQuerySet(models.QuerySet):
    def live(self):
        return self.filter(voided_at__isnull=True)


class OwnerRemittance(PublicIdModel, TimeStampedModel):
    """A payment made to a property owner against one month's statement. Voided, never deleted."""

    class Method(models.TextChoices):
        MPESA = "MPESA", _("M-Pesa")
        BANK = "BANK", _("Bank transfer")
        CASH = "CASH", _("Cash")
        CHEQUE = "CHEQUE", _("Cheque")
        OTHER = "OTHER", _("Other")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    owner = models.ForeignKey("properties.PropertyOwner", on_delete=models.PROTECT, related_name="remittances")
    # The first day of the statement month it pays.
    month = models.DateField(_("statement month"))
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    paid_on = models.DateField(_("date paid"))
    method = models.CharField(_("method"), max_length=10, choices=Method.choices, default=Method.MPESA)
    reference = models.CharField(_("reference"), max_length=60, blank=True)
    note = models.CharField(_("note"), max_length=300, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+")
    void_reason = models.CharField(_("reason"), max_length=300, blank=True)

    objects = RemittanceQuerySet.as_manager()

    class Meta:
        ordering = ["-paid_on", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="reports_remittance_amount_positive"),
            models.CheckConstraint(condition=Q(month__day=1), name="reports_remittance_month_first_day"),
        ]

    def __str__(self):
        return f"{self.owner} {self.month:%Y-%m} {self.amount}"

    @property
    def is_void(self) -> bool:
        return self.voided_at is not None


class OwnerStatementSend(PublicIdModel):
    """One statement sent to an owner: the PDF and the figures as they were then."""

    class EmailStatus(models.TextChoices):
        NONE = "NONE", _("Not emailed")
        SENT = "SENT", _("Emailed")
        FAILED = "FAILED", _("Email failed")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    owner = models.ForeignKey("properties.PropertyOwner", on_delete=models.PROTECT, related_name="statement_sends")
    month = models.DateField()
    number = models.CharField(max_length=30)
    # Private: served only through a view that checks the member may see the statement.
    pdf = models.FileField(upload_to="owner-statements/%Y/", editable=False)
    collected = models.DecimalField(max_digits=14, decimal_places=2)
    fee = models.DecimalField(max_digits=14, decimal_places=2)
    expenses = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # D-067
    due = models.DecimalField(max_digits=14, decimal_places=2)
    remitted = models.DecimalField(max_digits=14, decimal_places=2)
    email_to = models.EmailField(blank=True)
    email_status = models.CharField(max_length=10, choices=EmailStatus.choices, default=EmailStatus.NONE)
    email_error = models.CharField(max_length=300, blank=True)
    sms = models.ForeignKey("notifications.Message", on_delete=models.SET_NULL, null=True, blank=True,
                            related_name="+")
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="+")
    sent_at = models.DateTimeField()

    class Meta:
        ordering = ["-sent_at", "-pk"]
        constraints = [
            models.UniqueConstraint("organization", "number", name="reports_statementsend_number_unique"),
        ]

    def __str__(self):
        return self.number

    @property
    def remaining(self):
        return self.due - self.remitted


class DailySnapshot(models.Model):
    """What was overdue on one property at the end of one day (D-077).

    Arrears over time is the one trend the ledger cannot rebuild as it was known then: a payment
    recorded late, or back-dated, changes the past. The daily job keeps the figure as it stood.
    A row the backfill made from today's ledger is marked `rebuilt`. Rows are only written by the job.
    """

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="+")
    property = models.ForeignKey("properties.Property", on_delete=models.CASCADE, related_name="+")
    day = models.DateField()
    # Overdue amounts by days past due plus grace (billing.selectors.AGING_BUCKETS, "not yet due" left out).
    d1_30 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    d31_60 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    d61_90 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    d90 = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    leases = models.PositiveIntegerField(default=0)  # leases with money overdue
    rebuilt = models.BooleanField(default=False)
    taken_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["day", "property_id"]
        constraints = [
            models.UniqueConstraint("property", "day", name="reports_snapshot_property_day_unique"),
        ]
        indexes = [models.Index(fields=["organization", "day"])]

    def __str__(self):
        return f"{self.property_id} {self.day}"

    def arrears(self):  # a method: the `property` field hides the decorator in this class body
        return self.d1_30 + self.d31_60 + self.d61_90 + self.d90


class MonthlyReportSent(models.Model):  # noqa: DJ008 - a bookkeeping row never shown by name
    """The monthly report emailed to a member for a month, so a second run does not send it again (D-194)."""

    membership = models.ForeignKey("accounts.Membership", on_delete=models.CASCADE, related_name="+")
    month = models.DateField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint("membership", "month", name="reports_monthly_sent_unique")]
