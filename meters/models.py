"""Metered water (D-057).

- A meter belongs to a property and serves one unit (its own meter) or several (shared).
- Readings wait for approval. Approving one works out what each lease owes (MeterCharge).
- Billing puts each charge on an invoice line that points back at it, so it is billed once.
"""

import builtins
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from core.models import (
    AllObjectsManager,
    ArchivableModel,
    LiveManager,
    PublicIdModel,
    ScopedQuerySet,
    TimeStampedModel,
)


class Meter(PublicIdModel, TimeStampedModel, ArchivableModel):
    class Kind(models.TextChoices):
        POSTPAID = "POSTPAID", _("Postpaid (billed from readings)")
        PREPAID = "PREPAID", _("Prepaid (not billed)")

    class Split(models.TextChoices):
        EQUAL = "EQUAL", _("Equally")
        WEIGHTED = "WEIGHTED", _("By weight")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    property = models.ForeignKey("properties.Property", on_delete=models.PROTECT, related_name="meters")
    label = models.CharField(_("label"), max_length=60, help_text=_("e.g. A1 water, or the serial number"))
    serial = models.CharField(_("serial number"), max_length=40, blank=True)
    kind = models.CharField(_("kind"), max_length=10, choices=Kind.choices, default=Kind.POSTPAID)
    split = models.CharField(_("shared meter split"), max_length=10, choices=Split.choices, default=Split.EQUAL)
    # KES per cubic metre. The rate in force when a reading is approved is stored on the reading.
    rate = models.DecimalField(_("rate per m³"), max_digits=10, decimal_places=2, default=Decimal("0.00"))
    # Per unit, per reading. Zero means none.
    minimum_charge = models.DecimalField(_("minimum charge"), max_digits=10, decimal_places=2,
                                         default=Decimal("0.00"))
    units = models.ManyToManyField("properties.Unit", through="MeterUnit", related_name="meters")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        ordering = ["property__name", "label"]
        constraints = [
            models.CheckConstraint(condition=Q(rate__gte=0, minimum_charge__gte=0),
                                   name="meters_meter_rates_not_negative"),
        ]

    def __str__(self):
        return self.label

    # builtins.property: the `property` field shadows the builtin in this class body.
    @builtins.property
    def is_prepaid(self) -> bool:
        return self.kind == self.Kind.PREPAID


class MeterUnit(models.Model):
    meter = models.ForeignKey(Meter, on_delete=models.CASCADE, related_name="served")
    unit = models.ForeignKey("properties.Unit", on_delete=models.PROTECT, related_name="+")
    # Used when the meter splits by weight, e.g. 2 for a two-bedroom and 1 for a bedsitter.
    weight = models.DecimalField(_("weight"), max_digits=6, decimal_places=2, default=Decimal("1.00"))

    class Meta:
        ordering = ["unit__code"]
        constraints = [
            models.UniqueConstraint("meter", "unit", name="meters_meterunit_once"),
            models.CheckConstraint(condition=Q(weight__gt=0), name="meters_meterunit_weight_positive"),
        ]

    def __str__(self):
        return f"{self.meter} · {self.unit.code}"


class MeterReading(PublicIdModel, TimeStampedModel):
    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", _("Waiting for approval")
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")

    class Flag(models.TextChoices):
        LOWER = "LOWER", _("Lower than last time")
        ZERO = "ZERO", _("No water used while let")
        HIGH = "HIGH", _("Much higher than usual")
        LONG_GAP = "LONG_GAP", _("More than 45 days since the last reading")

    LIVE = (Status.SUBMITTED, Status.APPROVED)

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    meter = models.ForeignKey(Meter, on_delete=models.PROTECT, related_name="readings")
    read_on = models.DateField(_("reading date"))
    value = models.DecimalField(_("reading (m³)"), max_digits=12, decimal_places=3)
    # The first reading, or the first after a new meter was fitted: starts the count, bills nothing.
    is_baseline = models.BooleanField(_("meter replaced / first reading"), default=False)
    # The live reading this one is measured from, fixed when it is recorded.
    previous = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                                 editable=False)
    flags = models.JSONField(default=list, blank=True, editable=False)
    note = models.CharField(_("note"), max_length=300, blank=True)
    photo = models.ImageField(upload_to="meter-readings/%Y/", blank=True, editable=False)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.SUBMITTED,
                              editable=False)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    # Filled in on approval.
    rate = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, editable=False)
    minimum_charge = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, editable=False)
    approved_at = models.DateTimeField(null=True, blank=True, editable=False)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+", editable=False)
    approval_note = models.CharField(max_length=300, blank=True, editable=False)
    rejected_at = models.DateTimeField(null=True, blank=True, editable=False)
    rejected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+", editable=False)
    reject_reason = models.CharField(max_length=300, blank=True, editable=False)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-read_on", "-pk"]
        constraints = [
            models.UniqueConstraint("meter", "read_on", condition=Q(status__in=["SUBMITTED", "APPROVED"]),
                                    name="meters_reading_one_per_day"),
            models.CheckConstraint(condition=Q(value__gte=0), name="meters_reading_value_not_negative"),
            models.CheckConstraint(condition=Q(is_baseline=True) | Q(previous__isnull=False),
                                   name="meters_reading_measured_from_previous"),
        ]
        indexes = [models.Index(fields=["organization", "status"]), models.Index(fields=["meter", "read_on"])]

    def __str__(self):
        return f"{self.meter} · {self.read_on:%d/%m/%Y} · {self.value}"

    @property
    def usage(self) -> Decimal | None:
        if self.is_baseline or self.previous is None:
            return None
        return self.value - self.previous.value

    @property
    def days(self) -> int | None:
        if self.is_baseline or self.previous is None:
            return None
        return (self.read_on - self.previous.read_on).days

    @property
    def flag_labels(self) -> list[str]:
        labels = dict(self.Flag.choices)
        return [str(labels.get(f, f)) for f in self.flags]


class MeterCharge(models.Model):
    """What one lease owes for one approved reading. Billed through an InvoiceLine that points here."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    reading = models.ForeignKey(MeterReading, on_delete=models.PROTECT, related_name="charges")
    lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, related_name="meter_charges")
    unit = models.ForeignKey("properties.Unit", on_delete=models.PROTECT, related_name="+")
    # The days of the reading period this lease covered.
    service_start = models.DateField()
    service_end = models.DateField()
    # First day of the month whose invoice carries it (D-057 item 6).
    billing_month = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    description = models.CharField(max_length=200)
    # Set when the approval is undone; a cancelled charge is never billed.
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="meters_charge_amount_positive"),
            models.CheckConstraint(condition=Q(service_end__gte=F("service_start")), name="meters_charge_days_order"),
        ]
        indexes = [models.Index(fields=["lease", "billing_month"])]

    def __str__(self):
        return self.description
