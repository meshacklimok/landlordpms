"""Billing (doc 11 §8): what is owed. Payments, what was paid, come in Phase 4.

Money rules are in doc 11 §25 and core/money.py. The ledger is signed (D-042): positive means
the tenant owes more, negative is a credit. Deposits have a ledger of their own (doc 14 A1).
"""

import datetime
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from core.models import (
    AllObjectsManager,
    ArchivableModel,
    LiveManager,
    PublicIdModel,
    ScopedQuerySet,
    TimeStampedModel,
)

ZERO = Decimal("0.00")


class ChargeType(PublicIdModel, TimeStampedModel, ArchivableModel):
    """An organization's kinds of charge, e.g. Water or Service charge.

    The category says how billing treats it. Rent and deposit are not recurring
    lease charges: rent comes from LeaseRentChange and the deposit from the lease.
    """

    class Category(models.TextChoices):
        RENT = "RENT", _("Rent")
        DEPOSIT = "DEPOSIT", _("Deposit")
        WATER = "WATER", _("Water")
        ELECTRICITY = "ELECTRICITY", _("Electricity")
        GARBAGE = "GARBAGE", _("Garbage")
        SERVICE_CHARGE = "SERVICE_CHARGE", _("Service charge")
        SECURITY = "SECURITY", _("Security")
        PARKING = "PARKING", _("Parking")
        LATE_FEE = "LATE_FEE", _("Late fee")
        OTHER = "OTHER", _("Other")

    # Categories that cannot be a recurring lease charge.
    NOT_RECURRING = (Category.RENT, Category.DEPOSIT, Category.LATE_FEE)

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    name = models.CharField(_("name"), max_length=60)
    category = models.CharField(_("category"), max_length=20, choices=Category.choices, default=Category.OTHER)
    # Seeded for every organization; renamable but not archivable.
    is_system = models.BooleanField(default=False, editable=False)
    # D-034: design-in only. Nothing calculates tax until an accountant confirms the rules.
    is_taxable = models.BooleanField(default=False, editable=False)
    tax_rate = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True, editable=False)

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint("organization", Upper("name"), name="billing_chargetype_org_name_unique"),
        ]

    def __str__(self):
        return self.name

    @property
    def is_recurring_allowed(self) -> bool:
        return self.category not in self.NOT_RECURRING


# ---------------------------------------------------------------------------
# Invoices and the ledger
# ---------------------------------------------------------------------------


class AppendOnlyModel(models.Model):
    """Rows are written once. Corrections are new rows (doc 11 §21: never deleted)."""

    # Fields a service may still fill in after the row exists.
    MUTABLE_FIELDS: tuple[str, ...] = ()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            fields = kwargs.get("update_fields")
            if fields is None or not set(fields) <= set(self.MUTABLE_FIELDS):
                raise ValueError(f"{type(self).__name__} rows are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(f"{type(self).__name__} rows are never deleted.")


class Invoice(PublicIdModel, TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Draft")
        ISSUED = "ISSUED", _("Issued")
        PARTIALLY_PAID = "PARTIALLY_PAID", _("Partly paid")
        PAID = "PAID", _("Paid")
        VOID = "VOID", _("Void")

    OPEN = (Status.ISSUED, Status.PARTIALLY_PAID)

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, related_name="invoices")
    # INV-2026-000124, assigned when issued; drafts have none. Never reused, even when voided.
    number = models.CharField(_("invoice number"), max_length=30, blank=True, editable=False)
    status = models.CharField(_("status"), max_length=16, choices=Status.choices, default=Status.DRAFT,
                              editable=False)
    period_start = models.DateField(_("period start"))
    period_end = models.DateField(_("period end"))
    issue_date = models.DateField(_("issue date"), null=True, blank=True)
    due_date = models.DateField(_("due date"))
    # due_date plus the lease's grace days. Unpaid after this day means overdue (derived, doc 11 §8).
    overdue_after = models.DateField(_("overdue after"))
    currency = models.CharField(max_length=3, default="KES", editable=False)
    # Totals are sums of rounded lines and are never rounded again (doc 11 §25).
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    tax_total = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    # Kept by payment allocation (Phase 4).
    amount_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0, editable=False)
    notes = models.CharField(_("notes"), max_length=300, blank=True)
    issued_at = models.DateTimeField(null=True, blank=True, editable=False)
    voided_at = models.DateTimeField(null=True, blank=True, editable=False)
    void_reason = models.TextField(blank=True, editable=False)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+", editable=False)
    # Null when the monthly job made it.
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-period_start", "-pk"]
        constraints = [
            models.UniqueConstraint("organization", "number", condition=~Q(number=""),
                                    name="billing_invoice_org_number_unique"),
            models.CheckConstraint(condition=Q(period_end__gte=F("period_start")),
                                   name="billing_invoice_period_order"),
            models.CheckConstraint(condition=Q(total=F("subtotal") + F("tax_total")),
                                   name="billing_invoice_total_sum"),
            models.CheckConstraint(condition=Q(subtotal__gte=0, tax_total__gte=0),
                                   name="billing_invoice_amounts_not_negative"),
            models.CheckConstraint(condition=Q(amount_paid__gte=0, amount_paid__lte=F("total")),
                                   name="billing_invoice_paid_within_total"),
            models.CheckConstraint(condition=Q(status="DRAFT") | ~Q(number=""),
                                   name="billing_invoice_issued_has_number"),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "overdue_after"]),
            models.Index(fields=["lease", "period_start"]),
        ]

    def __str__(self):
        return self.number or str(_("Draft invoice"))

    @property
    def outstanding(self) -> Decimal:
        if self.status in (self.Status.DRAFT, self.Status.VOID):
            return ZERO
        return self.total - self.amount_paid

    def is_overdue(self, today: datetime.date) -> bool:
        return self.status in self.OPEN and today > self.overdue_after and self.outstanding > 0


class InvoiceLine(models.Model):
    """One charge on an invoice. Amounts are rounded here, at the line (doc 11 §25)."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="lines")
    lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, related_name="+")
    charge_type = models.ForeignKey(ChargeType, on_delete=models.PROTECT, related_name="+")
    description = models.CharField(_("description"), max_length=200)
    quantity = models.DecimalField(_("quantity"), max_digits=14, decimal_places=4, default=1)
    unit_price = models.DecimalField(_("unit price"), max_digits=14, decimal_places=2)
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # The days this line covers.
    service_start = models.DateField(null=True, blank=True)
    service_end = models.DateField(null=True, blank=True)
    # First day of the billed month, on lines the monthly job makes; null on hand-made lines.
    # Unique per lease and charge type, so running the job twice never bills twice (doc 11 §8).
    billing_month = models.DateField(null=True, blank=True, editable=False)
    # Set when the invoice is voided, which frees the month to be billed again.
    is_void = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint("lease", "billing_month", "charge_type", condition=Q(is_void=False),
                                    name="billing_invoiceline_billed_once"),
            models.CheckConstraint(condition=Q(amount__gte=0, tax_amount__gte=0, quantity__gt=0),
                                   name="billing_invoiceline_amounts_not_negative"),
            models.CheckConstraint(
                condition=Q(service_start__isnull=True) | Q(service_end__isnull=True)
                | Q(service_end__gte=F("service_start")),
                name="billing_invoiceline_service_order"),
        ]

    def __str__(self):
        return f"{self.description} {self.amount}"


class LedgerEntry(AppendOnlyModel):
    """What a lease owes, one signed row per event (D-042).

    Positive = the tenant owes more (debit); negative = a credit. Balance = sum of amounts.
    Nothing is edited or deleted; a mistake is corrected by a REVERSAL entry.
    """

    class Kind(models.TextChoices):
        OPENING_BALANCE = "OPENING_BALANCE", _("Balance brought forward")
        INVOICE = "INVOICE", _("Invoice")
        INVOICE_VOID = "INVOICE_VOID", _("Invoice voided")
        PAYMENT = "PAYMENT", _("Payment")
        PAYMENT_REVERSAL = "PAYMENT_REVERSAL", _("Payment reversed")
        ADJUSTMENT = "ADJUSTMENT", _("Adjustment")
        # A deposit deduction put towards what the tenant owes (doc 14 A1).
        DEPOSIT_APPLIED = "DEPOSIT_APPLIED", _("Deposit applied")
        REVERSAL = "REVERSAL", _("Correction")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, related_name="ledger_entries")
    entry_date = models.DateField(_("date"))
    kind = models.CharField(_("kind"), max_length=20, choices=Kind.choices)
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="KES")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    # A REVERSAL points at the entry it cancels; each entry is reversed at most once.
    reversal_of = models.OneToOneField("self", on_delete=models.PROTECT, null=True, blank=True,
                                       related_name="reversed_by")
    reason = models.CharField(_("reason"), max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["entry_date", "pk"]
        verbose_name_plural = "ledger entries"
        constraints = [
            models.CheckConstraint(condition=~Q(amount=0), name="billing_ledger_amount_not_zero"),
            models.CheckConstraint(condition=~Q(kind__in=["INVOICE", "PAYMENT_REVERSAL"]) | Q(amount__gt=0),
                                   name="billing_ledger_debit_kinds_positive"),
            models.CheckConstraint(
                condition=~Q(kind__in=["INVOICE_VOID", "PAYMENT", "DEPOSIT_APPLIED"]) | Q(amount__lt=0),
                name="billing_ledger_credit_kinds_negative"),
            models.CheckConstraint(
                condition=(Q(kind="REVERSAL", reversal_of__isnull=False)
                           | (~Q(kind="REVERSAL") & Q(reversal_of__isnull=True))),
                name="billing_ledger_reversal_has_target"),
            models.CheckConstraint(condition=~Q(kind__in=["INVOICE", "INVOICE_VOID"]) | Q(invoice__isnull=False),
                                   name="billing_ledger_invoice_kinds_link"),
            # An invoice is posted once and voided once.
            models.UniqueConstraint("invoice", "kind", condition=Q(kind__in=["INVOICE", "INVOICE_VOID"]),
                                    name="billing_ledger_invoice_posted_once"),
        ]
        indexes = [models.Index(fields=["lease", "entry_date"]), models.Index(fields=["organization", "kind"])]

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount}"


class DepositEntry(AppendOnlyModel):
    """The deposit sub-ledger (doc 14 A1), apart from LedgerEntry so a deposit never offsets rent.

    Positive = more held for the tenant; negative = less. Held = sum of amounts per lease and type.
    """

    class Kind(models.TextChoices):
        RECEIVED = "DEPOSIT_RECEIVED", _("Deposit received")
        DEDUCTION = "DEPOSIT_DEDUCTION", _("Deduction")
        REFUNDED = "DEPOSIT_REFUNDED", _("Refunded")
        # A linked pair: out of the old lease, into the new one (D-016).
        TRANSFERRED = "DEPOSIT_TRANSFERRED", _("Transferred")
        REVERSAL = "REVERSAL", _("Correction")

    class DepositType(models.TextChoices):
        RENT = "RENT", _("Rent deposit")
        WATER = "WATER", _("Water deposit")
        ELECTRICITY = "ELECTRICITY", _("Electricity deposit")
        OTHER = "OTHER", _("Other deposit")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, related_name="deposit_entries")
    deposit_type = models.CharField(_("deposit"), max_length=12, choices=DepositType.choices,
                                    default=DepositType.RENT)
    entry_date = models.DateField(_("date"))
    kind = models.CharField(_("kind"), max_length=20, choices=Kind.choices)
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default="KES")
    reason = models.CharField(_("reason"), max_length=300, blank=True)
    # How the money came in or went out, e.g. an M-Pesa code.
    reference = models.CharField(_("reference"), max_length=60, blank=True)
    # The other half of a transfer.
    counterpart = models.OneToOneField("self", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    # The rent credit a deduction was put towards, if any.
    ledger_entry = models.OneToOneField(LedgerEntry, on_delete=models.PROTECT, null=True, blank=True,
                                        related_name="deposit_entry")
    reversal_of = models.OneToOneField("self", on_delete=models.PROTECT, null=True, blank=True,
                                       related_name="reversed_by")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    # Filled in once, when the second half of a transfer is written.
    MUTABLE_FIELDS = ("counterpart",)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["entry_date", "pk"]
        verbose_name_plural = "deposit entries"
        constraints = [
            models.CheckConstraint(condition=~Q(amount=0), name="billing_deposit_amount_not_zero"),
            models.CheckConstraint(condition=~Q(kind="DEPOSIT_RECEIVED") | Q(amount__gt=0),
                                   name="billing_deposit_received_positive"),
            models.CheckConstraint(
                condition=~Q(kind__in=["DEPOSIT_DEDUCTION", "DEPOSIT_REFUNDED"]) | Q(amount__lt=0),
                name="billing_deposit_outgoing_negative"),
            models.CheckConstraint(condition=~Q(kind="DEPOSIT_DEDUCTION") | ~Q(reason=""),
                                   name="billing_deposit_deduction_has_reason"),
            models.CheckConstraint(
                condition=(Q(kind="REVERSAL", reversal_of__isnull=False)
                           | (~Q(kind="REVERSAL") & Q(reversal_of__isnull=True))),
                name="billing_deposit_reversal_has_target"),
        ]
        indexes = [models.Index(fields=["lease", "deposit_type"])]

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount}"
