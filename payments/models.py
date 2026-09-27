"""Payments and receipts (doc 11 §9-10, D-043): what was paid, separate from what is owed.

Money rules are in doc 11 §25 and core/money.py. A payment posts one signed credit to the lease
ledger (billing.LedgerEntry) once confirmed; PaymentAllocation only tracks which invoices it
settles. Nothing here is ever deleted — a mistake is corrected by rejection or reversal.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import (
    AllObjectsManager,
    ArchivableModel,
    LiveManager,
    PublicIdModel,
    ScopedQuerySet,
    TimeStampedModel,
)
from core.money import ZERO


class PaymentAccount(PublicIdModel, TimeStampedModel, ArchivableModel):
    """Where money is received: a Paybill, Till, bank account, personal M-Pesa line or cash."""

    class Type(models.TextChoices):
        PAYBILL = "PAYBILL", _("M-Pesa Paybill")
        TILL = "TILL", _("M-Pesa Till")
        BANK = "BANK", _("Bank account")
        MPESA_PERSONAL = "MPESA_PERSONAL", _("Personal M-Pesa")
        CASH = "CASH", _("Cash")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    type = models.CharField(_("type"), max_length=20, choices=Type.choices)
    number = models.CharField(_("account number"), max_length=40, blank=True)
    display_name = models.CharField(_("display name"), max_length=80)

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        ordering = ["display_name"]
        constraints = [
            models.UniqueConstraint("organization", "type", "number",
                                    name="payments_paymentaccount_org_type_number_unique"),
        ]

    def __str__(self):
        return self.display_name


class PropertyPaymentAccount(TimeStampedModel):
    """Which payment accounts a property collects rent on. A property may have several."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    property = models.ForeignKey("properties.Property", on_delete=models.PROTECT, related_name="payment_accounts")
    payment_account = models.ForeignKey(PaymentAccount, on_delete=models.PROTECT, related_name="properties")
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default", "pk"]
        constraints = [
            models.UniqueConstraint("property", "payment_account",
                                    name="payments_propertypaymentaccount_unique"),
            models.UniqueConstraint("property", condition=Q(is_default=True),
                                    name="payments_propertypaymentaccount_one_default"),
        ]

    def __str__(self):
        return f"{self.property} → {self.payment_account}"


class Payment(PublicIdModel, TimeStampedModel):
    """Money a tenant paid, before it is confirmed and allocated to invoices."""

    class Method(models.TextChoices):
        MPESA = "MPESA", _("M-Pesa")
        CASH = "CASH", _("Cash")
        BANK = "BANK", _("Bank")
        CHEQUE = "CHEQUE", _("Cheque")
        OTHER = "OTHER", _("Other")

    class Status(models.TextChoices):
        PENDING_REVIEW = "PENDING_REVIEW", _("Pending review")
        CONFIRMED = "CONFIRMED", _("Confirmed")
        REVERSED = "REVERSED", _("Reversed")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, related_name="payments")
    # Who paid, if known; defaults to the lease's primary tenant when left blank.
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    method = models.CharField(_("method"), max_length=10, choices=Method.choices)
    status = models.CharField(_("status"), max_length=16, choices=Status.choices, default=Status.PENDING_REVIEW,
                              editable=False)
    paid_at = models.DateField(_("date paid"))
    reference = models.CharField(_("reference"), max_length=60, blank=True)
    payment_account = models.ForeignKey(PaymentAccount, on_delete=models.PROTECT, null=True, blank=True,
                                        related_name="+")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="+", editable=False)
    confirmed_at = models.DateTimeField(null=True, blank=True, editable=False)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+", editable=False)
    reversed_at = models.DateTimeField(null=True, blank=True, editable=False)
    reversal_reason = models.CharField(max_length=300, blank=True, editable=False)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-paid_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payments_payment_amount_positive"),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["lease", "paid_at"]),
        ]

    def __str__(self):
        return f"{self.get_method_display()} {self.amount}"

    # REVERSED covers two outcomes: turned down in review (never confirmed) or undone after confirming.
    STATES = {
        "pending": _("Pending review"),
        "confirmed": _("Confirmed"),
        "rejected": _("Rejected"),
        "reversed": _("Reversed"),
    }

    @property
    def state(self) -> str:
        if self.status == self.Status.PENDING_REVIEW:
            return "pending"
        if self.status == self.Status.CONFIRMED:
            return "confirmed"
        return "reversed" if self.confirmed_at else "rejected"

    def get_state_display(self) -> str:
        return self.STATES[self.state]

    @property
    def allocated(self):
        return sum((a.amount for a in self.allocations.all()), ZERO)

    @property
    def unallocated(self):
        return self.amount - self.allocated


class PaymentAllocation(models.Model):
    """Which invoice(s) a confirmed payment settles, and how much of each."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    invoice = models.ForeignKey("billing.Invoice", on_delete=models.PROTECT, related_name="payment_allocations")
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint("payment", "invoice", name="payments_paymentallocation_unique"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="payments_paymentallocation_amount_positive"),
        ]

    def __str__(self):
        return f"{self.payment} → {self.invoice} {self.amount}"


class Receipt(TimeStampedModel):
    """A numbered PDF proving a confirmed payment (doc 11 §9). Kept even if the payment is reversed."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="receipt")
    number = models.CharField(_("receipt number"), max_length=30, editable=False)
    issued_at = models.DateTimeField(editable=False)
    pdf = models.FileField(upload_to="receipts/%Y/", editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint("organization", "number", name="payments_receipt_org_number_unique"),
        ]

    def __str__(self):
        return self.number
