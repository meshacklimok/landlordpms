"""Billing (doc 11 §8). Phase 2 brings only ChargeType forward; invoices and the ledger come in Phase 3."""

from django.db import models
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from core.models import AllObjectsManager, ArchivableModel, LiveManager, PublicIdModel, TimeStampedModel


class ChargeType(PublicIdModel, TimeStampedModel, ArchivableModel):
    """An organization's kinds of charge, e.g. Water or Service charge.

    The category says how billing treats it. Rent and deposit are not recurring
    lease charges: rent comes from LeaseRentChange and the deposit from the lease.
    """

    class Category(models.TextChoices):
        RENT = "RENT", _("Rent")
        DEPOSIT = "DEPOSIT", _("Deposit")
        WATER = "WATER", _("Water")
        ELECTRICITY = "ELECTRICITY", _("Electricity")
        GARBAGE = "GARBAGE", _("Garbage")
        SERVICE_CHARGE = "SERVICE_CHARGE", _("Service charge")
        SECURITY = "SECURITY", _("Security")
        PARKING = "PARKING", _("Parking")
        LATE_FEE = "LATE_FEE", _("Late fee")
        OTHER = "OTHER", _("Other")

    # Categories that cannot be a recurring lease charge.
    NOT_RECURRING = (Category.RENT, Category.DEPOSIT, Category.LATE_FEE)

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    name = models.CharField(_("name"), max_length=60)
    category = models.CharField(_("category"), max_length=20, choices=Category.choices, default=Category.OTHER)
    # Seeded for every organization; renamable but not archivable.
    is_system = models.BooleanField(default=False, editable=False)

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint("organization", Upper("name"), name="billing_chargetype_org_name_unique"),
        ]

    def __str__(self):
        return self.name

    @property
    def is_recurring_allowed(self) -> bool:
        return self.category not in self.NOT_RECURRING
