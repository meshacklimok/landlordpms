"""Shared abstract models and managers used by every app.

Conventions (doc 11 §20, §23):
- Business foreign keys use ``on_delete=PROTECT``.
- Anything exposed in a URL or webhook has a UUID ``public_id``.
- Archivable models are hidden by ``objects`` and visible through ``all_objects``.
- Organization-scoped models are always read through ``.for_org(org)``.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PublicIdModel(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    class Meta:
        abstract = True


class ScopedQuerySet(models.QuerySet):
    """QuerySet with organization scoping and archive helpers."""

    def for_org(self, organization):
        if organization is None:
            raise ValueError("for_org() needs an organization; refusing to return unscoped data.")
        return self.filter(organization=organization)

    def archived(self):
        return self.filter(archived_at__isnull=False)

    def not_archived(self):
        return self.filter(archived_at__isnull=True)


class LiveManager(models.Manager.from_queryset(ScopedQuerySet)):
    """Default manager for archivable models: hides archived rows."""

    def get_queryset(self):
        return super().get_queryset().filter(archived_at__isnull=True)


class AllObjectsManager(models.Manager.from_queryset(ScopedQuerySet)):
    """Includes archived rows. Use for history, restore and admin."""


class ArchivableModel(models.Model):
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def archive(self, by) -> None:
        """Low-level flag change. Call from a service that checks capability and audits."""
        self.archived_at = timezone.now()
        self.archived_by = by
        self.save(update_fields=["archived_at", "archived_by", *self._extra_touch_fields()])

    def restore(self) -> None:
        self.archived_at = None
        self.archived_by = None
        self.save(update_fields=["archived_at", "archived_by", *self._extra_touch_fields()])

    def _extra_touch_fields(self) -> list[str]:
        return ["updated_at"] if any(f.name == "updated_at" for f in self._meta.fields) else []


class OrgScopedModel(models.Model):
    """Every business table carries its organization (doc 03, D-004)."""

    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.PROTECT,
        related_name="+",
    )

    objects = models.Manager.from_queryset(ScopedQuerySet)()

    class Meta:
        abstract = True


class NumberSequence(models.Model):
    """Human document numbers per organization, e.g. LSE-2026-000042 (doc 11 §24).

    Allocated inside the issuing transaction under a row lock: numbers are never reused,
    and a rolled-back issue gives its number back. Use ``core.numbering.next_number``.
    """

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    key = models.CharField(max_length=30)
    # The year for yearly series, "" for a series that never resets.
    period = models.CharField(max_length=10, blank=True)
    prefix = models.CharField(max_length=10)
    next_value = models.PositiveBigIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint("organization", "key", "period", name="core_numbersequence_unique"),
        ]

    def __str__(self):
        return f"{self.prefix} {self.period} → {self.next_value}"
