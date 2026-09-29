"""Statement imports and bank lines (D-064).

A `StatementImport` is one uploaded CSV for one payment account: read into a preview first, then
applied. Applying a bank statement adds `BankTransaction` lines; an M-Pesa statement adds
`mpesa.MpesaTransaction` rows instead, so M-Pesa money has one inbox.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import PublicIdModel, ScopedQuerySet, TimeStampedModel


class StatementImport(PublicIdModel, TimeStampedModel):
    class Kind(models.TextChoices):
        BANK = "BANK", _("Bank statement")
        MPESA = "MPESA", _("M-Pesa statement")

    class Status(models.TextChoices):
        PREVIEW = "PREVIEW", _("Checked, not imported")
        APPLIED = "APPLIED", _("Imported")
        DISCARDED = "DISCARDED", _("Discarded")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    payment_account = models.ForeignKey("payments.PaymentAccount", on_delete=models.PROTECT,
                                        related_name="statement_imports")
    kind = models.CharField(max_length=5, choices=Kind.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PREVIEW)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    file_name = models.CharField(max_length=200, blank=True)
    # The new money-in lines, as read; emptied once applied or when the preview expires.
    rows = models.JSONField(default=list, blank=True)
    # [[line number, message]] for lines that could not be read.
    errors = models.JSONField(default=list, blank=True)
    new_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    matched_count = models.PositiveIntegerField(default=0)
    unmatched_count = models.PositiveIntegerField(default=0)
    applied_at = models.DateTimeField(null=True, blank=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["organization", "-created_at"])]

    def __str__(self):
        return f"{self.get_kind_display()} {self.created_at:%Y-%m-%d %H:%M}"


class BankTransaction(PublicIdModel, TimeStampedModel):
    """One money-in line from a bank statement. Never deleted; money that is not rent is IGNORED."""

    class Status(models.TextChoices):
        MATCHED = "MATCHED", _("Matched")
        UNMATCHED = "UNMATCHED", _("Waiting to be matched")
        IGNORED = "IGNORED", _("Ignored")

    class MatchedBy(models.TextChoices):
        REFERENCE = "REFERENCE", _("Unit reference")
        MANUAL = "MANUAL", _("By hand")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    payment_account = models.ForeignKey("payments.PaymentAccount", on_delete=models.PROTECT,
                                        related_name="bank_transactions")
    statement = models.ForeignKey(StatementImport, on_delete=models.PROTECT, related_name="bank_lines")
    posted_on = models.DateField(_("date"))
    description = models.CharField(_("description"), max_length=300, blank=True)
    reference = models.CharField(_("reference"), max_length=60, blank=True)
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    balance = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    # The same line in an overlapping statement has the same fingerprint (D-064 item 4).
    fingerprint = models.CharField(max_length=64)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNMATCHED)
    note = models.CharField(max_length=300, blank=True)
    suggested_lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, null=True, blank=True,
                                        related_name="+")
    payment = models.OneToOneField("payments.Payment", on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="bank_transaction")
    matched_by = models.CharField(max_length=10, choices=MatchedBy.choices, blank=True)
    matched_by_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                        blank=True, related_name="+")
    matched_at = models.DateTimeField(null=True, blank=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-posted_on", "-pk"]
        constraints = [
            models.UniqueConstraint("payment_account", "fingerprint", name="banking_transaction_fingerprint_unique"),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="banking_transaction_amount_positive"),
            models.CheckConstraint(condition=~models.Q(status="MATCHED") | models.Q(payment__isnull=False),
                                   name="banking_transaction_matched_has_payment"),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["payment_account", "posted_on"]),
        ]

    def __str__(self):
        return f"{self.posted_on} · {self.amount}"

    @property
    def label(self) -> str:
        return self.reference or self.description[:60]
