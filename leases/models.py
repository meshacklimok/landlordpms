"""Leases (doc 11 §7, D-016, D-018, doc 14 A3/A4).

- A lease is for one unit and has one or more tenants (LeaseTenant), exactly one of them primary.
- Rent is never a field that gets edited: it is a list of LeaseRentChange rows, and the rent
  on a date is the latest change on or before it.
- Recurring extras (water, garbage, service charge) are LeaseCharge rows.
- LeasePayer holds extra phone numbers that pay for this lease (spouse, employer). Matching
  payments to them comes in Phase 6.
- Stored statuses: DRAFT, ACTIVE, ENDED, TERMINATED, RENEWED. "Expiring" is derived.
- A closed lease records the day it actually ended (ended_on). It can be in the future
  when a renewal or transfer is activated ahead of the move.
- The database refuses two issued leases on one unit whose dates overlap.
"""

import datetime

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeOperators
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Func, Q, Value
from django.db.models.functions import Coalesce
from django.utils.translation import gettext_lazy as _

from core.models import AllObjectsManager, ArchivableModel, LiveManager, PublicIdModel, TimeStampedModel

EXPIRING_WITHIN_DAYS = 60


class DateRange(Func):
    """PostgreSQL daterange(start, end, '[]'); a null end is open-ended."""

    function = "daterange"
    output_field = DateRangeField()


class Lease(PublicIdModel, TimeStampedModel, ArchivableModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Draft")
        ACTIVE = "ACTIVE", _("Active")
        ENDED = "ENDED", _("Ended")
        TERMINATED = "TERMINATED", _("Terminated")
        RENEWED = "RENEWED", _("Renewed")

    class Frequency(models.TextChoices):
        # One invoice per period, counted from the lease's start month (D-049).
        MONTHLY = "MONTHLY", _("Monthly")
        QUARTERLY = "QUARTERLY", _("Quarterly")
        YEARLY = "YEARLY", _("Yearly")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    unit = models.ForeignKey("properties.Unit", on_delete=models.PROTECT, related_name="leases")
    # LSE-2026-000042, assigned on activation (doc 11 §24). Drafts have none.
    number = models.CharField(_("lease number"), max_length=30, blank=True, editable=False)
    status = models.CharField(_("status"), max_length=12, choices=Status.choices, default=Status.DRAFT,
                              editable=False)

    start_date = models.DateField(_("start date"))
    # Null means periodic: it runs until someone ends it.
    end_date = models.DateField(_("end date"), null=True, blank=True)
    billing_frequency = models.CharField(_("billing"), max_length=10, choices=Frequency.choices,
                                         default=Frequency.MONTHLY)
    due_day = models.PositiveSmallIntegerField(
        _("rent due day"), default=1, validators=[MinValueValidator(1), MaxValueValidator(28)],
        help_text=_("Day of the month rent is due (1–28)."),
    )
    grace_days = models.PositiveSmallIntegerField(
        _("grace days"), default=3, validators=[MaxValueValidator(31)],
        help_text=_("Days after the due date before rent counts as late."),
    )
    notice_days = models.PositiveSmallIntegerField(_("notice period (days)"), default=30,
                                                   validators=[MaxValueValidator(365)])
    # The agreed deposit. What is actually held lives in the deposit ledger (Phase 3).
    deposit_amount = models.DecimalField(_("deposit"), max_digits=14, decimal_places=2, default=0,
                                         validators=[MinValueValidator(0)])
    currency = models.CharField(max_length=3, default="KES", editable=False)
    terms = models.TextField(_("terms and notes"), blank=True)
    previous_lease = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True,
                                       related_name="next_leases", editable=False)

    # Filled by lease actions (step 4).
    activated_at = models.DateTimeField(null=True, blank=True, editable=False)
    notice_given_on = models.DateField(null=True, blank=True, editable=False)
    ended_on = models.DateField(null=True, blank=True, editable=False)
    end_reason = models.TextField(blank=True, editable=False)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        ordering = ["-start_date", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                                   name="leases_lease_end_after_start"),
            models.CheckConstraint(condition=Q(due_day__gte=1, due_day__lte=28), name="leases_lease_due_day_range"),
            models.CheckConstraint(condition=Q(deposit_amount__gte=0), name="leases_lease_deposit_not_negative"),
            models.UniqueConstraint("organization", "number", condition=~Q(number=""),
                                    name="leases_lease_org_number_unique"),
            models.CheckConstraint(condition=Q(ended_on__isnull=True) | Q(ended_on__gte=F("start_date")),
                                   name="leases_lease_ended_after_start"),
            # D-016: never two issued leases on one unit at the same time. A closed lease
            # holds the unit until the day it actually ended.
            ExclusionConstraint(
                name="leases_lease_no_overlap",
                expressions=[
                    ("unit", RangeOperators.EQUAL),
                    (DateRange("start_date", Coalesce("ended_on", "end_date"), Value("[]")),
                     RangeOperators.OVERLAPS),
                ],
                condition=~Q(status="DRAFT"),
            ),
        ]
        indexes = [models.Index(fields=["organization", "status"]), models.Index(fields=["unit", "status"])]

    def __str__(self):
        return self.number or f"{_('Draft lease')} · {self.unit.code}"

    @property
    def months_per_period(self) -> int:
        return MONTHS_PER_PERIOD[self.billing_frequency]

    @property
    def rent_per_period(self):
        """Today's monthly rent times the months each invoice covers (D-049)."""
        rent = self.current_rent
        return rent * self.months_per_period if rent is not None else None

    @property
    def is_draft(self) -> bool:
        return self.status == self.Status.DRAFT

    def rent_on(self, day: datetime.date):
        change = self.rent_changes.filter(effective_from__lte=day).order_by("-effective_from").first()
        return change.amount if change else None

    @property
    def current_rent(self):
        from django.utils import timezone

        today = timezone.localdate()
        return self.rent_on(max(today, self.start_date))

    @property
    def effective_end(self):
        """The day the lease actually ends: when it was closed, else its contract end (None = periodic)."""
        return self.ended_on or self.end_date

    @property
    def move_out_by(self):
        """The last day under a notice to vacate (doc 14 B1)."""
        if self.notice_given_on is None:
            return None
        return self.notice_given_on + datetime.timedelta(days=self.notice_days)

    def is_expiring(self, today: datetime.date) -> bool:
        return (self.status == self.Status.ACTIVE and self.end_date is not None
                and today <= self.end_date <= today + datetime.timedelta(days=EXPIRING_WITHIN_DAYS))

    @property
    def primary_tenant(self):
        link = next((lt for lt in self.lease_tenants.all() if lt.is_primary), None)
        return link.tenant if link else None


MONTHS_PER_PERIOD = {Lease.Frequency.MONTHLY: 1, Lease.Frequency.QUARTERLY: 3, Lease.Frequency.YEARLY: 12}


class LeaseTenant(TimeStampedModel):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    lease = models.ForeignKey(Lease, on_delete=models.PROTECT, related_name="lease_tenants")
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="lease_links")
    # The tenant invoices and receipts are addressed to.
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_primary", "tenant__name"]
        constraints = [
            models.UniqueConstraint("lease", "tenant", name="leases_leasetenant_unique"),
            models.UniqueConstraint("lease", condition=Q(is_primary=True), name="leases_leasetenant_one_primary"),
        ]

    def __str__(self):
        return f"{self.tenant} on {self.lease}"


class LeaseRentChange(models.Model):
    """Rent history. Rows are added, never edited, once the lease is active (D-018)."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    lease = models.ForeignKey(Lease, on_delete=models.PROTECT, related_name="rent_changes")
    effective_from = models.DateField(_("effective from"))
    amount = models.DecimalField(_("monthly rent"), max_digits=14, decimal_places=2)
    reason = models.CharField(_("reason"), max_length=200, blank=True)
    # Kenyan practice: tenants get written notice of an increase (doc 14).
    notice_sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["effective_from"]
        constraints = [
            models.UniqueConstraint("lease", "effective_from", name="leases_rentchange_one_per_day"),
            models.CheckConstraint(condition=Q(amount__gt=0), name="leases_rentchange_amount_positive"),
        ]

    def __str__(self):
        return f"{self.amount} from {self.effective_from}"


class LeaseCharge(TimeStampedModel):
    """A fixed recurring extra on a lease, e.g. KES 500 garbage a month."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    lease = models.ForeignKey(Lease, on_delete=models.PROTECT, related_name="charges")
    charge_type = models.ForeignKey("billing.ChargeType", on_delete=models.PROTECT, related_name="lease_charges")
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    # The amount is per month. Charges are billed with the lease's own frequency (D-049), so
    # this stays MONTHLY.
    frequency = models.CharField(_("billing"), max_length=10, choices=Lease.Frequency.choices,
                                 default=Lease.Frequency.MONTHLY)
    active_from = models.DateField(_("from"))
    active_to = models.DateField(_("until"), null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    class Meta:
        ordering = ["active_from", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="leases_leasecharge_amount_positive"),
            models.CheckConstraint(condition=Q(active_to__isnull=True) | Q(active_to__gte=F("active_from")),
                                   name="leases_leasecharge_to_after_from"),
        ]

    def __str__(self):
        return f"{self.charge_type} {self.amount}"

    def is_active_on(self, day: datetime.date) -> bool:
        return self.active_from <= day and (self.active_to is None or day <= self.active_to)


class LeasePayer(TimeStampedModel):
    """Another phone that pays for this lease (doc 14 A4). Used by payment matching in Phase 6."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    lease = models.ForeignKey(Lease, on_delete=models.PROTECT, related_name="payers")
    phone = models.CharField(_("phone"), max_length=16)
    name = models.CharField(_("name"), max_length=150, blank=True, help_text=_("e.g. employer, spouse"))
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    class Meta:
        ordering = ["pk"]
        constraints = [models.UniqueConstraint("lease", "phone", name="leases_leasepayer_unique")]
        indexes = [models.Index(fields=["organization", "phone"])]

    def __str__(self):
        return self.phone
