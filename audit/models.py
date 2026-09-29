"""Append-only audit log (doc 11 §12). Never edited, never deleted (D-028)."""

from django.conf import settings
from django.db import models


class AuditEventQuerySet(models.QuerySet):
    def for_org(self, organization):
        if organization is None:
            raise ValueError("for_org() needs an organization.")
        return self.filter(organization=organization)

    def update(self, **kwargs):
        raise PermissionError("Audit events are append-only.")

    def delete(self):
        raise PermissionError("Audit events cannot be deleted.")


class AuditEvent(models.Model):
    # Null for platform-level events such as login or sign-up before an organization exists.
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    action = models.CharField(max_length=64, db_index=True)
    object_type = models.CharField(max_length=64, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)
    # {"field": [old, new]} or free-form details.
    changes = models.JSONField(default=dict, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AuditEventQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["organization", "-created_at"]),
            models.Index(fields=["object_type", "object_id"]),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor} {self.action}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("Audit events are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit events cannot be deleted.")
