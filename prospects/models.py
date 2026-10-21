"""Prospects and viewings: a light CRM for filling vacancies (D-072).

- A prospect is someone looking to rent at one property, and maybe one unit. They are never deleted:
  a prospect who does not rent is closed as lost with a reason, and can be reopened.
- The status follows what happened (contacted, viewing booked, viewed, unit held); staff do not set it by hand.
- A hold reserves one unit for one prospect until a date. It sets the unit's manual status to Reserved.
- Converting creates (or links) a tenant; the lease is then drafted as usual.
"""

import builtins
import datetime

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import PublicIdModel, ScopedQuerySet, TimeStampedModel


class Prospect(PublicIdModel, TimeStampedModel):
    class Status(models.TextChoices):
        NEW = "NEW", _("New")
        CONTACTED = "CONTACTED", _("Contacted")
        VIEWING = "VIEWING", _("Viewing booked")
        VIEWED = "VIEWED", _("Viewed")
        RESERVED = "RESERVED", _("Unit held")
        CONVERTED = "CONVERTED", _("Became a tenant")
        LOST = "LOST", _("Lost")

    class Source(models.TextChoices):
        WALK_IN = "WALK_IN", _("Walk-in")
        PHONE = "PHONE", _("Phone call")
        WHATSAPP = "WHATSAPP", _("WhatsApp")
        VACANCY_LINK = "VACANCY_LINK", _("Vacancy link")
        REFERRAL = "REFERRAL", _("Referral")
        SIGN_BOARD = "SIGN_BOARD", _("Sign board")
        ONLINE = "ONLINE", _("Online listing")
        OTHER = "OTHER", _("Other")

    class LostReason(models.TextChoices):
        PRICE = "PRICE", _("Rent too high")
        UNIT = "UNIT", _("Unit not suitable")
        LOCATION = "LOCATION", _("Location")
        ELSEWHERE = "ELSEWHERE", _("Rented elsewhere")
        NO_RESPONSE = "NO_RESPONSE", _("Stopped responding")
        NOT_QUALIFIED = "NOT_QUALIFIED", _("Did not qualify")
        OTHER = "OTHER", _("Other")

    OPEN = (Status.NEW, Status.CONTACTED, Status.VIEWING, Status.VIEWED, Status.RESERVED)
    # Longest a unit may be held for one prospect (D-072 item 4).
    MAX_HOLD_DAYS = 30

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    property = models.ForeignKey("properties.Property", on_delete=models.PROTECT, related_name="prospects")
    # The unit they asked about, if any.
    unit = models.ForeignKey("properties.Unit", on_delete=models.PROTECT, null=True, blank=True,
                             related_name="prospects")
    name = models.CharField(_("name"), max_length=150)
    phone = models.CharField(_("phone"), max_length=16)
    email = models.EmailField(_("email"), blank=True)
    source = models.CharField(_("how they found us"), max_length=12, choices=Source.choices, default=Source.WALK_IN)
    budget = models.DecimalField(_("budget per month"), max_digits=12, decimal_places=2, null=True, blank=True)
    move_in_by = models.DateField(_("wants to move in by"), null=True, blank=True)
    wants = models.CharField(_("looking for"), max_length=300, blank=True)
    # A hostel enquiry (D-189): who the bed is for, and whether nothing suitable was free when they asked.
    gender = models.CharField(_("gender"), max_length=1, blank=True, choices=[("F", _("Female")), ("M", _("Male"))])
    waitlisted = models.BooleanField(_("on the waitlist"), default=False, editable=False)
    # When the student was texted that a bed is free (D-190). Once only: after the offer lapses staff follow up.
    offered_at = models.DateTimeField(null=True, blank=True, editable=False)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.NEW,
                              editable=False)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="prospects", editable=False)
    next_contact_on = models.DateField(_("follow up on"), null=True, blank=True)
    contacted_at = models.DateTimeField(null=True, blank=True, editable=False)
    # D-072 item 4: one unit held for this prospect until a date.
    held_unit = models.ForeignKey("properties.Unit", on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+", editable=False)
    held_until = models.DateField(null=True, blank=True, editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, null=True, blank=True,
                               related_name="prospects", editable=False)
    converted_at = models.DateTimeField(null=True, blank=True, editable=False)
    lost_at = models.DateTimeField(null=True, blank=True, editable=False)
    lost_reason = models.CharField(max_length=14, choices=LostReason.choices, blank=True, editable=False)
    lost_note = models.CharField(max_length=300, blank=True, editable=False)
    # Empty for an enquiry from a public vacancy link.
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+", editable=False)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            # A unit is held for one prospect at a time.
            models.UniqueConstraint(fields=["held_unit"], condition=Q(held_unit__isnull=False),
                                    name="prospect_one_hold_per_unit"),
            models.CheckConstraint(condition=Q(held_unit__isnull=True) | Q(held_until__isnull=False),
                                   name="prospect_hold_has_date"),
        ]
        indexes = [models.Index(fields=["organization", "status"]),
                   models.Index(fields=["organization", "phone"]),
                   models.Index(fields=["property", "status"]),
                   models.Index(fields=["assigned_to", "status"])]

    def __str__(self):
        return self.name

    @builtins.property
    def is_open(self) -> bool:
        return self.status in self.OPEN

    def follow_up_due(self, today: datetime.date | None = None) -> bool:
        return (self.is_open and self.next_contact_on is not None
                and self.next_contact_on <= (today or timezone.localdate()))

    def hold_lapsed(self, today: datetime.date | None = None) -> bool:
        return self.held_unit_id is not None and self.held_until < (today or timezone.localdate())


class Viewing(PublicIdModel):
    class Status(models.TextChoices):
        BOOKED = "BOOKED", _("Booked")
        DONE = "DONE", _("Viewed")
        NO_SHOW = "NO_SHOW", _("Did not come")
        CANCELLED = "CANCELLED", _("Cancelled")

    prospect = models.ForeignKey(Prospect, on_delete=models.PROTECT, related_name="viewings")
    unit = models.ForeignKey("properties.Unit", on_delete=models.PROTECT, related_name="viewings")
    scheduled_at = models.DateTimeField(_("when"))
    # The staff member showing the unit.
    with_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="viewings")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.BOOKED, editable=False)
    outcome = models.CharField(_("how it went"), max_length=300, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["scheduled_at", "pk"]
        indexes = [models.Index(fields=["prospect", "status"]),
                   models.Index(fields=["unit", "scheduled_at"]),
                   models.Index(fields=["status", "scheduled_at"])]

    def __str__(self):
        return f"{self.prospect} {self.unit}"


class ProspectNote(models.Model):
    """One line of a prospect's history."""

    class Kind(models.TextChoices):
        CREATED = "CREATED", _("Added")
        ENQUIRY = "ENQUIRY", _("Enquiry")
        CONTACT = "CONTACT", _("Contacted")
        NOTE = "NOTE", _("Note")
        VIEWING = "VIEWING", _("Viewing")
        HOLD = "HOLD", _("Unit held")
        RELEASE = "RELEASE", _("Hold released")
        ASSIGNED = "ASSIGNED", _("Assigned")
        CONVERTED = "CONVERTED", _("Became a tenant")
        LOST = "LOST", _("Lost")
        REOPENED = "REOPENED", _("Reopened")

    prospect = models.ForeignKey(Prospect, on_delete=models.PROTECT, related_name="notes")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    text = models.CharField(max_length=1000, blank=True)
    # Empty for an enquiry from a public vacancy link.
    by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                           related_name="+")
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["at", "pk"]
        indexes = [models.Index(fields=["prospect", "at"])]

    def __str__(self):
        return f"{self.prospect} {self.kind}"
