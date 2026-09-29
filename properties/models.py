"""Properties. Phase 1 has only what access scoping needs; Phase 2 adds
buildings, units, addresses and the design-in fields (doc 11 §5, D-035)."""

from django.conf import settings
from django.db import models
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from core.models import AllObjectsManager, ArchivableModel, LiveManager, PublicIdModel, TimeStampedModel


class Property(PublicIdModel, TimeStampedModel, ArchivableModel):
    class Category(models.TextChoices):
        APARTMENT_BLOCK = "APARTMENT_BLOCK", _("Apartment block")
        ESTATE = "ESTATE", _("Estate")
        COMMERCIAL = "COMMERCIAL", _("Commercial")
        MIXED_USE = "MIXED_USE", _("Mixed-use")
        SINGLE_HOUSE = "SINGLE_HOUSE", _("Single house")

    # One managing organization only (D-017).
    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="properties")
    name = models.CharField(_("name"), max_length=150)
    # Short code used in payment references, e.g. GV in GV-A102 (D-029).
    code = models.CharField(_("code"), max_length=10)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.APARTMENT_BLOCK)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        verbose_name_plural = "properties"
        ordering = ["name"]
        constraints = [
            # Holds across archived rows so old codes are never reused (doc 11 §23).
            models.UniqueConstraint("organization", Upper("code"), name="properties_property_org_code_unique"),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.code = (self.code or "").strip().upper()
        super().save(*args, **kwargs)
