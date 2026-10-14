"""Status page incidents (D-061) and support requests (D-063)."""

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import PublicIdModel, TimeStampedModel


class Incident(TimeStampedModel):
    """Written by a Platform Admin for the public status page. Never names an organization."""

    class Impact(models.TextChoices):
        MINOR = "MINOR", _("Minor")
        MAJOR = "MAJOR", _("Major")
        OUTAGE = "OUTAGE", _("Outage")

    class Status(models.TextChoices):
        INVESTIGATING = "INVESTIGATING", _("Investigating")
        IDENTIFIED = "IDENTIFIED", _("Identified")
        MONITORING = "MONITORING", _("Monitoring")
        RESOLVED = "RESOLVED", _("Resolved")

    title = models.CharField(max_length=150)
    impact = models.CharField(max_length=10, choices=Impact.choices, default=Impact.MINOR)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.INVESTIGATING)
    started_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    is_published = models.BooleanField(default=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.status == self.Status.RESOLVED and self.resolved_at is None:
            self.resolved_at = timezone.now()
        elif self.status != self.Status.RESOLVED:
            self.resolved_at = None
        super().save(*args, **kwargs)


class IncidentUpdate(models.Model):
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name="updates")
    status = models.CharField(max_length=15, choices=Incident.Status.choices)
    text = models.TextField(max_length=1000)
    posted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-posted_at"]

    def __str__(self):
        return f"{self.get_status_display()} {self.posted_at:%Y-%m-%d %H:%M}"


def _attachment_path(instance, filename):
    import uuid
    from pathlib import Path

    return f"support/{timezone.now():%Y}/{uuid.uuid4().hex}{Path(filename).suffix.lower()[:10]}"


class SupportRequest(PublicIdModel, TimeStampedModel):
    class Kind(models.TextChoices):
        QUESTION = "QUESTION", _("A question")
        PROBLEM = "PROBLEM", _("Something is not working")
        IMPORT_HELP = "IMPORT_HELP", _("Import my data for me")
        DELETION = "DELETION", _("Delete my account")

    class Status(models.TextChoices):
        OPEN = "OPEN", _("Open")
        WAITING = "WAITING", _("Waiting on the customer")
        CLOSED = "CLOSED", _("Closed")

    number = models.CharField(max_length=30, unique=True)
    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="support_requests")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.QUESTION)
    subject = models.CharField(max_length=150)
    message = models.TextField(max_length=5000)
    attachment = models.FileField(upload_to=_attachment_path, blank=True, editable=False)
    attachment_name = models.CharField(max_length=200, blank=True, editable=False)
    page = models.CharField(max_length=300, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    internal_note = models.TextField(blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.number} {self.subject}"


class AppOpen(models.Model):
    """How often the installed app is opened, per day (D-099 item 3, D-102). Counts only: no user, IP or browser."""

    class Audience(models.TextChoices):
        STAFF = "STAFF", _("Staff")
        TENANT = "TENANT", _("Tenant")
        SIGNED_OUT = "SIGNED_OUT", _("Signed out")

    class Platform(models.TextChoices):
        ANDROID = "ANDROID", _("Android")
        IOS = "IOS", _("iPhone")
        OTHER = "OTHER", _("Other")

    day = models.DateField()
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, null=True, blank=True,
                                     related_name="+")  # staff only
    audience = models.CharField(max_length=10, choices=Audience.choices)
    platform = models.CharField(max_length=8, choices=Platform.choices)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["day", "organization", "audience", "platform"],
                                               nulls_distinct=False, name="support_appopen_one_row")]
        ordering = ["-day"]

    def __str__(self):
        return f"{self.day} {self.audience} {self.platform}: {self.count}"
