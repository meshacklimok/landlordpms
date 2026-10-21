"""Tenancy and payment record letters (D-048), notices to vacate and demand letters (D-109).

A letter states facts from the ledger as they stood when it was issued: the tenancy, and
whichever of payment record, balance, deposit and rent the organization chose to show. The
facts are frozen in `facts` and drawn into a stored PDF. A private code on the letter opens a
public page that shows the same facts, so a landlord or lender can check a copy is genuine.
"""

import datetime

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import PublicIdModel, TimeStampedModel


class TenancyLetter(PublicIdModel, TimeStampedModel):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    # The latest lease of the tenancy when the letter was issued; earlier renewals and moves are in `facts`.
    lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, related_name="letters")
    number = models.CharField(_("letter number"), max_length=30, editable=False)
    issued_at = models.DateTimeField(editable=False)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
                                  editable=False)
    facts = models.JSONField(editable=False)
    pdf = models.FileField(upload_to="letters/%Y/", editable=False)
    # Printed on the letter; opens /l/<code>/ without a login.
    verify_code = models.CharField(max_length=32, unique=True, editable=False)
    withdrawn_at = models.DateTimeField(null=True, blank=True, editable=False)
    withdrawn_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="+", editable=False)
    withdraw_reason = models.CharField(max_length=300, blank=True, editable=False)

    class Meta:
        ordering = ["-issued_at", "-pk"]
        constraints = [
            models.UniqueConstraint("organization", "number", name="letters_letter_org_number_unique"),
            models.CheckConstraint(condition=Q(withdrawn_at__isnull=True) | ~Q(withdraw_reason=""),
                                   name="letters_letter_withdrawn_has_reason"),
        ]

    def __str__(self):
        return self.number

    @property
    def is_withdrawn(self) -> bool:
        return self.withdrawn_at is not None


class NoticeLetter(PublicIdModel, TimeStampedModel):
    """A notice to vacate or a demand for rent arrears, and how it was served (D-109).

    Frozen like a tenancy letter: the facts and the PDF are fixed when it is issued and a code opens the same
    check page. Only the record of service changes afterwards, and each change is audited.
    """

    class Kind(models.TextChoices):
        VACATE = "VACATE", _("Notice to vacate")
        DEMAND = "DEMAND", _("Demand for rent arrears")

    class ServedVia(models.TextChoices):
        HAND = "HAND", _("Handed to the tenant")
        ADULT = "ADULT", _("Handed to an adult at the premises")
        AFFIXED = "AFFIXED", _("Fixed to the door of the premises")
        REGISTERED_POST = "REGISTERED_POST", _("Registered post")
        COURIER = "COURIER", _("Courier")
        EMAIL = "EMAIL", _("Email")
        OTHER = "OTHER", _("Other")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, related_name="notice_letters")
    kind = models.CharField(_("kind"), max_length=10, choices=Kind.choices)
    # The landlord's notice a notice to vacate was made from; empty for a demand.
    notice = models.ForeignKey("leases.LeaseNotice", on_delete=models.PROTECT, null=True, blank=True,
                               related_name="letters")
    number = models.CharField(_("letter number"), max_length=30, editable=False)
    issued_at = models.DateTimeField(editable=False)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
                                  editable=False)
    facts = models.JSONField(editable=False)
    pdf = models.FileField(upload_to="letters/%Y/", editable=False)
    verify_code = models.CharField(max_length=32, unique=True, editable=False)
    withdrawn_at = models.DateTimeField(null=True, blank=True, editable=False)
    withdrawn_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="+", editable=False)
    withdraw_reason = models.CharField(max_length=300, blank=True, editable=False)

    served_on = models.DateField(_("served on"), null=True, blank=True)
    served_via = models.CharField(_("how it was served"), max_length=16, choices=ServedVia.choices, blank=True)
    served_by_name = models.CharField(_("served by"), max_length=150, blank=True)
    received_by = models.CharField(_("received by"), max_length=150, blank=True)
    service_note = models.CharField(_("note"), max_length=300, blank=True)
    # A scan or photo of the signed copy, kept with the lease's documents.
    proof = models.ForeignKey("documents.Document", on_delete=models.PROTECT, null=True, blank=True,
                              related_name="+")
    service_recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                            blank=True, related_name="+")
    service_recorded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-issued_at", "-pk"]
        constraints = [
            models.UniqueConstraint("organization", "number", name="letters_notice_org_number_unique"),
            models.UniqueConstraint("notice", condition=Q(withdrawn_at__isnull=True),
                                    name="letters_notice_one_live_per_notice"),
            models.CheckConstraint(condition=Q(withdrawn_at__isnull=True) | ~Q(withdraw_reason=""),
                                   name="letters_notice_withdrawn_has_reason"),
            models.CheckConstraint(condition=Q(kind="VACATE", notice__isnull=False)
                                   | Q(kind="DEMAND", notice__isnull=True),
                                   name="letters_notice_vacate_has_notice"),
            models.CheckConstraint(condition=Q(served_on__isnull=True, served_via="")
                                   | (Q(served_on__isnull=False) & ~Q(served_via="")),
                                   name="letters_notice_served_has_how"),
        ]

    def __str__(self):
        return self.number

    @property
    def is_withdrawn(self) -> bool:
        return self.withdrawn_at is not None

    @property
    def is_served(self) -> bool:
        return self.served_on is not None

    @property
    def pay_by(self):
        """The last day to pay a served demand: its days count from the day it was served."""
        if self.kind != self.Kind.DEMAND or self.served_on is None:
            return None
        return self.served_on + datetime.timedelta(days=self.facts["pay_within_days"])
