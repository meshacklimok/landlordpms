"""Move-in and move-out condition reports (D-047).

- A unit has an item register (UnitItem): what is in it and where.
- A report belongs to an issued lease, one per kind unless cancelled. Starting it copies the
  register into its lines; completing it locks it for good.
- Photos are re-encoded on upload and only served through a checked view.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import AllObjectsManager, ArchivableModel, LiveManager, PublicIdModel, TimeStampedModel


class UnitItem(TimeStampedModel, ArchivableModel):
    """One thing in a unit that a report checks, e.g. Kitchen · Sink and taps."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    unit = models.ForeignKey("properties.Unit", on_delete=models.PROTECT, related_name="items")
    area = models.CharField(_("area"), max_length=60, blank=True, help_text=_("e.g. Kitchen, Bedroom 1"))
    name = models.CharField(_("item"), max_length=100)
    quantity = models.PositiveSmallIntegerField(_("quantity"), default=1)
    notes = models.CharField(_("note"), max_length=200, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        ordering = ["sort_order", "pk"]

    def __str__(self):
        return f"{self.area} · {self.name}" if self.area else self.name


class ConditionReport(PublicIdModel, TimeStampedModel):
    class Kind(models.TextChoices):
        MOVE_IN = "MOVE_IN", _("Move-in")
        MOVE_OUT = "MOVE_OUT", _("Move-out")

    class Status(models.TextChoices):
        DRAFT = "DRAFT", _("Draft")
        COMPLETED = "COMPLETED", _("Completed")
        CANCELLED = "CANCELLED", _("Cancelled")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, related_name="condition_reports")
    unit = models.ForeignKey("properties.Unit", on_delete=models.PROTECT, related_name="condition_reports")
    kind = models.CharField(_("kind"), max_length=10, choices=Kind.choices)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.DRAFT,
                              editable=False)

    inspected_on = models.DateField(_("inspection date"))
    inspected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    tenant_present = models.BooleanField(_("tenant was present"), default=False)
    tenant_comments = models.TextField(_("tenant's comments"), blank=True)
    keys_handed = models.PositiveSmallIntegerField(_("keys handed over"), null=True, blank=True)
    notes = models.TextField(_("general notes"), blank=True)

    completed_at = models.DateTimeField(null=True, blank=True, editable=False)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="+", editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="+", editable=False)
    cancel_reason = models.CharField(max_length=300, blank=True, editable=False)

    class Meta:
        ordering = ["-inspected_on", "-pk"]
        constraints = [
            models.UniqueConstraint("lease", "kind", condition=~Q(status="CANCELLED"),
                                    name="inspections_report_one_per_lease_kind"),
        ]
        indexes = [models.Index(fields=["unit", "kind", "status"])]

    def __str__(self):
        return f"{self.get_kind_display()} · {self.unit.code} · {self.inspected_on:%d/%m/%Y}"

    @property
    def is_draft(self) -> bool:
        return self.status == self.Status.DRAFT


class Condition(models.TextChoices):
    GOOD = "GOOD", _("Good")
    FAIR = "FAIR", _("Fair")
    POOR = "POOR", _("Poor")
    DAMAGED = "DAMAGED", _("Damaged")
    MISSING = "MISSING", _("Missing")
    NOT_CHECKED = "NOT_CHECKED", _("Not checked")


# Worse is higher. "Not checked" has no rank and is never compared.
CONDITION_RANK = {Condition.GOOD: 1, Condition.FAIR: 2, Condition.POOR: 3, Condition.DAMAGED: 4,
                  Condition.MISSING: 5}


class ConditionReportLine(models.Model):
    """One register item as found on the day. Area, name and quantity are copied, not linked."""

    report = models.ForeignKey(ConditionReport, on_delete=models.PROTECT, related_name="lines")
    item = models.ForeignKey(UnitItem, on_delete=models.PROTECT, related_name="report_lines")
    area = models.CharField(max_length=60, blank=True)
    name = models.CharField(max_length=100)
    quantity = models.PositiveSmallIntegerField(default=1)
    condition = models.CharField(_("condition"), max_length=12, choices=Condition.choices, blank=True)
    notes = models.CharField(_("note"), max_length=300, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "pk"]
        constraints = [models.UniqueConstraint("report", "item", name="inspections_line_one_per_item")]

    def __str__(self):
        return f"{self.area} · {self.name}" if self.area else self.name


class ConditionPhoto(PublicIdModel):
    report = models.ForeignKey(ConditionReport, on_delete=models.PROTECT, related_name="photos")
    # Empty for a photo of the unit in general.
    line = models.ForeignKey(ConditionReportLine, on_delete=models.PROTECT, null=True, blank=True,
                             related_name="photos")
    image = models.ImageField(upload_to="condition-reports/%Y/", editable=False)
    width = models.PositiveIntegerField(editable=False)
    height = models.PositiveIntegerField(editable=False)
    caption = models.CharField(_("caption"), max_length=200, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at", "pk"]

    def __str__(self):
        return self.caption or self.image.name
