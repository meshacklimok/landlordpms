import datetime

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import PublicIdModel, TimeStampedModel

UNDO_WINDOW = datetime.timedelta(hours=24)


class ImportBatch(PublicIdModel, TimeStampedModel):
    """One uploaded CSV: checked first (PREVIEW), then applied, and undoable for 24 hours (doc 14 A2/B8)."""

    class Kind(models.TextChoices):
        UNITS = "UNITS", _("Units")
        TENANTS = "TENANTS", _("Tenants")
        BALANCES = "BALANCES", _("Opening balances")

    class Status(models.TextChoices):
        PREVIEW = "PREVIEW", _("Checked, not imported")
        APPLIED = "APPLIED", _("Imported")
        UNDONE = "UNDONE", _("Undone")
        DISCARDED = "DISCARDED", _("Discarded")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PREVIEW)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    file_name = models.CharField(max_length=200, blank=True)
    # [{"line": 2, "data": {...}, "errors": [...], "label": "GV-A1"}]. Sensitive values are dropped once applied.
    rows = models.JSONField(default=list, blank=True)
    ok_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    # Primary keys of the records this batch created, for undo.
    created_ids = models.JSONField(default=list, blank=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    undone_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "-created_at"])]

    def __str__(self):
        return f"{self.get_kind_display()} import {self.created_at:%Y-%m-%d %H:%M}"

    @property
    def undo_until(self):
        return self.applied_at + UNDO_WINDOW if self.applied_at else None

    @property
    def can_undo(self) -> bool:
        return self.status == self.Status.APPLIED and timezone.now() < self.undo_until
