"""Property expenses: money the landlord paid out to suppliers (flow C, D-027, D-067).

- An expense is for one property, already paid (cash basis), and is never edited or deleted:
  a wrong one is rejected or voided with a reason.
- It waits for approval unless the recorder may approve. Only approved expenses count in reports.
- Expenses never touch a tenant's balance.
"""

import builtins

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from core.models import AllObjectsManager, ArchivableModel, LiveManager, PublicIdModel, ScopedQuerySet, TimeStampedModel


class ExpenseCategory(PublicIdModel, TimeStampedModel, ArchivableModel):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    name = models.CharField(_("name"), max_length=60)

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "expense categories"
        constraints = [
            models.UniqueConstraint("organization", Lower("name"),
                                    condition=Q(archived_at__isnull=True), name="expenses_category_name_unique"),
        ]

    def __str__(self):
        return self.name


class Supplier(PublicIdModel, TimeStampedModel, ArchivableModel):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    name = models.CharField(_("name"), max_length=120)
    phone = models.CharField(_("phone"), max_length=20, blank=True)
    email = models.EmailField(_("email"), blank=True)
    kra_pin = models.CharField(_("KRA PIN"), max_length=11, blank=True)
    # Free text: "Till 123456", "Equity 0123…", "cash".
    payment_details = models.CharField(_("how they are paid"), max_length=200, blank=True)
    note = models.CharField(_("note"), max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Expense(PublicIdModel, TimeStampedModel):
    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", _("Waiting for approval")
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")
        VOIDED = "VOIDED", _("Voided")

    class Method(models.TextChoices):
        MPESA = "MPESA", _("M-Pesa")
        BANK = "BANK", _("Bank transfer")
        CASH = "CASH", _("Cash")
        CHEQUE = "CHEQUE", _("Cheque")
        OTHER = "OTHER", _("Other")

    LIVE = (Status.SUBMITTED, Status.APPROVED)

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    number = models.CharField(max_length=30, editable=False)
    property = models.ForeignKey("properties.Property", on_delete=models.PROTECT, related_name="expenses")
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT, related_name="expenses")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, null=True, blank=True, related_name="expenses")
    description = models.CharField(_("what was paid for"), max_length=200)
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    paid_on = models.DateField(_("date paid"))
    method = models.CharField(_("paid by"), max_length=10, choices=Method.choices, default=Method.MPESA)
    reference = models.CharField(_("reference"), max_length=60, blank=True)
    # Upper case, no spaces: the duplicate check (D-067 item 3).
    reference_key = models.CharField(max_length=60, blank=True, editable=False)
    # Private: a re-encoded JPEG or a checked PDF, served only through a view that checks scope.
    receipt = models.FileField(upload_to="expense-receipts/%Y/", blank=True, editable=False)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.SUBMITTED,
                              editable=False)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+", editable=False)
    rejected_at = models.DateTimeField(null=True, blank=True, editable=False)
    rejected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+", editable=False)
    reject_reason = models.CharField(max_length=300, blank=True, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+", editable=False)
    void_reason = models.CharField(max_length=300, blank=True, editable=False)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-paid_on", "-pk"]
        constraints = [
            models.UniqueConstraint("organization", "number", name="expenses_expense_number_unique"),
            models.UniqueConstraint("organization", "reference_key",
                                    condition=Q(status__in=["SUBMITTED", "APPROVED"]) & ~Q(reference_key=""),
                                    name="expenses_expense_reference_once"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="expenses_expense_amount_positive"),
        ]
        indexes = [models.Index(fields=["organization", "status", "paid_on"]),
                   models.Index(fields=["property", "paid_on"])]

    def __str__(self):
        return self.number

    @builtins.property
    def receipt_is_pdf(self) -> bool:
        return bool(self.receipt) and self.receipt.name.lower().endswith(".pdf")

    @builtins.property
    def is_live(self) -> bool:
        return self.status in self.LIVE
