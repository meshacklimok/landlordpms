"""Tenancy and payment record letters (D-048).

A letter states facts from the ledger as they stood when it was issued: the tenancy, and
whichever of payment record, balance, deposit and rent the organization chose to show. The
facts are frozen in `facts` and drawn into a stored PDF. A private code on the letter opens a
public page that shows the same facts, so a landlord or lender can check a copy is genuine.
"""

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
