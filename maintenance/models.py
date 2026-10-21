"""Maintenance requests and jobs (D-068).

- A request is for one property, and one unit or the common areas. It is never deleted: a wrong one
  is cancelled with a reason.
- Its history is a list of updates. Status changes are shown to the tenant; staff notes only when shared.
- Costs are expenses (D-067) linked to the request, approved like any other expense.
- Each request has a respond-by and a fix-by time from the organization's targets (D-074).
"""

import builtins
import datetime

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import PublicIdModel, ScopedQuerySet, TimeStampedModel


class MaintenanceRequest(PublicIdModel, TimeStampedModel):
    class Status(models.TextChoices):
        NEW = "NEW", _("New")
        ASSIGNED = "ASSIGNED", _("Assigned")
        IN_PROGRESS = "IN_PROGRESS", _("In progress")
        ON_HOLD = "ON_HOLD", _("On hold")
        DONE = "DONE", _("Done")
        CLOSED = "CLOSED", _("Closed")
        CANCELLED = "CANCELLED", _("Cancelled")

    class Priority(models.TextChoices):
        EMERGENCY = "EMERGENCY", _("Emergency")
        HIGH = "HIGH", _("High")
        NORMAL = "NORMAL", _("Normal")
        LOW = "LOW", _("Low")

    class Kind(models.TextChoices):
        PLUMBING = "PLUMBING", _("Plumbing")
        ELECTRICAL = "ELECTRICAL", _("Electrical")
        WATER = "WATER", _("Water supply")
        DOORS = "DOORS", _("Doors and locks")
        STRUCTURE = "STRUCTURE", _("Roof and walls")
        PAINTING = "PAINTING", _("Painting")
        APPLIANCES = "APPLIANCES", _("Appliances")
        PESTS = "PESTS", _("Pests")
        SECURITY = "SECURITY", _("Security")
        CLEANING = "CLEANING", _("Cleaning and garbage")
        METER = "METER", _("Meter problem")
        MISSED_PICKUP = "PICKUP", _("Missed pickup")
        OTHER = "OTHER", _("Other")

    class Source(models.TextChoices):
        STAFF = "STAFF", _("Staff")
        PORTAL = "PORTAL", _("Tenant portal")
        USSD = "USSD", _("USSD")
        ASSISTANT = "ASSISTANT", _("Assistant")

    OPEN = (Status.NEW, Status.ASSIGNED, Status.IN_PROGRESS, Status.ON_HOLD)
    # Worked on or waiting to be checked: the job is not finished for the office.
    UNFINISHED = (*OPEN, Status.DONE)
    # Default targets (D-068 item 2, D-074 item 1); an organization may set its own (MaintenanceTarget).
    RESPOND_AFTER = {
        Priority.EMERGENCY: datetime.timedelta(hours=4),
        Priority.HIGH: datetime.timedelta(days=1),
        Priority.NORMAL: datetime.timedelta(days=2),
        Priority.LOW: datetime.timedelta(days=7),
    }
    DUE_AFTER = {
        Priority.EMERGENCY: datetime.timedelta(hours=24),
        Priority.HIGH: datetime.timedelta(days=3),
        Priority.NORMAL: datetime.timedelta(days=7),
        Priority.LOW: datetime.timedelta(days=30),
    }

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    number = models.CharField(max_length=30, editable=False)
    property = models.ForeignKey("properties.Property", on_delete=models.PROTECT, related_name="maintenance_requests")
    # Empty for the common areas.
    unit = models.ForeignKey("properties.Unit", on_delete=models.PROTECT, null=True, blank=True,
                             related_name="maintenance_requests")
    # Set when the tenant of the unit's lease reported it: they follow it in the portal and get updates.
    lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, null=True, blank=True,
                              related_name="maintenance_requests")
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, null=True, blank=True,
                               related_name="maintenance_requests")
    title = models.CharField(_("what is wrong"), max_length=120)
    description = models.TextField(_("details"), max_length=2000, blank=True)
    kind = models.CharField(_("kind"), max_length=12, choices=Kind.choices, default=Kind.OTHER)
    priority = models.CharField(_("priority"), max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(_("status"), max_length=12, choices=Status.choices, default=Status.NEW,
                              editable=False)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.STAFF, editable=False)
    # D-074: work started by (respond by) and done by (fix by, `due_at`).
    respond_by = models.DateTimeField(_("respond by"))
    due_at = models.DateTimeField(_("due by"))
    # How often the fix-by time was moved by hand (D-074 item 3).
    due_moved = models.PositiveSmallIntegerField(default=0, editable=False)
    # Empty only for a report from the USSD menu, which has no user behind it (D-085 item 3).
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="maintenance_jobs", editable=False)
    supplier = models.ForeignKey("expenses.Supplier", on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="maintenance_requests", editable=False)
    # What the supplier quoted before the work, to set against what was spent (D-160). Not an expense.
    quote_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, editable=False)
    quote_note = models.CharField(max_length=200, blank=True, editable=False)
    quote_at = models.DateTimeField(null=True, blank=True, editable=False)
    # D-068 item 3: when each step first happened (reopening clears done and closed).
    assigned_at = models.DateTimeField(null=True, blank=True, editable=False)
    started_at = models.DateTimeField(null=True, blank=True, editable=False)
    done_at = models.DateTimeField(null=True, blank=True, editable=False)
    closed_at = models.DateTimeField(null=True, blank=True, editable=False)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+", editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancel_reason = models.CharField(max_length=300, blank=True, editable=False)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=Q(quote_amount__isnull=True) | Q(quote_amount__gt=0),
                                   name="maintenance_request_quote_positive"),
            models.UniqueConstraint("organization", "number", name="maintenance_request_number_unique"),
            models.CheckConstraint(condition=Q(reported_by__isnull=False) | Q(source__in=["USSD", "ASSISTANT"]),
                                   name="maintenance_request_has_reporter"),
        ]
        indexes = [models.Index(fields=["organization", "status", "due_at"]),
                   models.Index(fields=["property", "status"]),
                   models.Index(fields=["assigned_to", "status"])]

    def __str__(self):
        return self.number

    @builtins.property
    def is_open(self) -> bool:
        return self.status in self.OPEN

    def is_overdue(self, now: datetime.datetime | None = None) -> bool:
        return self.is_open and self.due_at < (now or timezone.now())

    @builtins.property
    def responded_at(self) -> datetime.datetime | None:
        """When work started; a job done or closed without being started responded then (D-074 item 1)."""
        return self.started_at or self.done_at

    @staticmethod
    def _target_state(at, target, now) -> str:
        if at is not None:
            return "met" if at <= target else "missed"
        return "late" if target < now else ""

    def response_state(self, now: datetime.datetime | None = None) -> str:
        """"met", "missed" (responded late), "late" (not yet and past the time) or "" (D-074 item 4)."""
        if self.status == self.Status.CANCELLED:
            return ""
        return self._target_state(self.responded_at, self.respond_by, now or timezone.now())

    def fix_state(self, now: datetime.datetime | None = None) -> str:
        if self.status == self.Status.CANCELLED:
            return ""
        return self._target_state(self.done_at, self.due_at, now or timezone.now())

    @builtins.property
    def where(self) -> str:
        return self.unit.code if self.unit_id else str(_("Common areas"))


class MaintenanceUpdate(models.Model):
    """One line of a request's history: a status change, an assignment, a note or a comment."""

    class Kind(models.TextChoices):
        REPORTED = "REPORTED", _("Reported")
        STATUS = "STATUS", _("Status")
        ASSIGNED = "ASSIGNED", _("Assigned")
        PRIORITY = "PRIORITY", _("Priority")
        NOTE = "NOTE", _("Note")
        COMMENT = "COMMENT", _("Tenant comment")
        PHOTO = "PHOTO", _("Photo")
        COST = "COST", _("Cost")

    request = models.ForeignKey(MaintenanceRequest, on_delete=models.PROTECT, related_name="updates")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    from_status = models.CharField(max_length=12, choices=MaintenanceRequest.Status.choices, blank=True)
    to_status = models.CharField(max_length=12, choices=MaintenanceRequest.Status.choices, blank=True)
    text = models.CharField(max_length=1000, blank=True)
    shared_with_tenant = models.BooleanField(default=False)
    # Empty for the "reported" line of a USSD report (D-085 item 3).
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                           related_name="+")
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["at", "pk"]
        indexes = [models.Index(fields=["request", "at"])]

    def __str__(self):
        return f"{self.request} {self.kind}"


class MaintenancePhoto(PublicIdModel):
    class Stage(models.TextChoices):
        REPORTED = "", _("Photo")
        BEFORE = "BEFORE", _("Before the work")
        AFTER = "AFTER", _("After the work")

    request =models.ForeignKey(MaintenanceRequest, on_delete=models.PROTECT, related_name="photos")
    # Private: a re-encoded JPEG, served only through a view that checks scope.
    image = models.ImageField(upload_to="maintenance/%Y/", editable=False)
    width = models.PositiveIntegerField(editable=False)
    height = models.PositiveIntegerField(editable=False)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(default=timezone.now)
    # Photos from the tenant, or that staff chose to show them.
    shared_with_tenant = models.BooleanField(default=False)
    # Before and after the work (D-160); empty for a photo of the report.
    stage = models.CharField(max_length=6, choices=Stage.choices, blank=True, default="", editable=False)

    class Meta:
        ordering = ["uploaded_at", "pk"]
        constraints = [models.CheckConstraint(condition=Q(width__gt=0), name="maintenance_photo_width")]

    def __str__(self):
        return self.image.name


class MaintenanceTarget(models.Model):
    """An organization's own respond-by and fix-by times for one priority (D-074 item 2)."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="+")
    priority = models.CharField(max_length=10, choices=MaintenanceRequest.Priority.choices)
    respond_hours = models.PositiveIntegerField()
    fix_hours = models.PositiveIntegerField()
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint("organization", "priority", name="maintenance_target_unique"),
            models.CheckConstraint(condition=Q(respond_hours__gt=0, fix_hours__gte=F("respond_hours")),
                                   name="maintenance_target_order"),
        ]

    def __str__(self):
        return f"{self.organization_id} {self.priority}"


class OfflineReport(models.Model):
    """A report typed on a phone without a connection and sent later; one request per id (D-090 item 3)."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    key = models.CharField(max_length=64)
    request = models.OneToOneField(MaintenanceRequest, on_delete=models.CASCADE, related_name="+")
    typed_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint("user", "key", name="maintenance_offlinereport_user_key_unique")]

    def __str__(self):
        return self.key
