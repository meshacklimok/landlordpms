"""Tenants: the people and companies who rent units (doc 11 §6, doc 14 A4, D4, D5).

"Tenant" always means the renter. The SaaS customer is `Organization`.

A tenant belongs to one organization. The same phone number may appear on several
tenants (shared family phones, the same person renting from two landlords), so
phone is indexed but not unique.

Only PROSPECT, ACTIVE and FORMER are stored. Leases move a tenant between them
(Phase 2 step 3); staff never set the status by hand.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from core.models import AllObjectsManager, ArchivableModel, LiveManager, PublicIdModel, TimeStampedModel


class Tenant(PublicIdModel, TimeStampedModel, ArchivableModel):
    class Kind(models.TextChoices):
        INDIVIDUAL = "INDIVIDUAL", _("Individual")
        COMPANY = "COMPANY", _("Company")

    class Status(models.TextChoices):
        PROSPECT = "PROSPECT", _("Prospect")
        ACTIVE = "ACTIVE", _("Active")
        FORMER = "FORMER", _("Former")

    class IdType(models.TextChoices):
        NATIONAL_ID = "NATIONAL_ID", _("National ID")
        PASSPORT = "PASSPORT", _("Passport")
        ALIEN_ID = "ALIEN_ID", _("Alien ID")
        COMPANY_REG = "COMPANY_REG", _("Company registration")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="tenants")
    kind = models.CharField(_("type"), max_length=12, choices=Kind.choices, default=Kind.INDIVIDUAL)
    # Full name, or the registered company name.
    name = models.CharField(_("name"), max_length=150)
    # For a company: the person we deal with.
    contact_person = models.CharField(_("contact person"), max_length=150, blank=True)
    phone = models.CharField(_("phone"), max_length=16, help_text=_("E.164, e.g. +254712345678"))
    alt_phone = models.CharField(_("other phone"), max_length=16, blank=True)
    email = models.EmailField(_("email"), blank=True)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.PROSPECT,
                              editable=False)

    # Sensitive: shown and edited only with tenants.view_sensitive.
    id_type = models.CharField(_("ID type"), max_length=12, choices=IdType.choices, blank=True)
    id_number = models.CharField(_("ID / registration number"), max_length=30, blank=True)
    kra_pin = models.CharField(_("KRA PIN"), max_length=11, blank=True)

    emergency_contact_name = models.CharField(_("emergency contact"), max_length=150, blank=True)
    emergency_contact_phone = models.CharField(_("emergency contact phone"), max_length=16, blank=True)
    notes = models.TextField(_("notes"), blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    objects = LiveManager()
    all_objects = AllObjectsManager()

    SENSITIVE_FIELDS = ("id_type", "id_number", "kra_pin")

    class Meta:
        ordering = ["name"]
        constraints = [
            # One tenant per ID document in an organization, archived rows included.
            models.UniqueConstraint(
                "organization", "id_type", Upper("id_number"),
                condition=~Q(id_number=""), name="tenants_tenant_org_id_number_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "phone"]),
            models.Index(fields=["organization", "status"]),
        ]

    def __str__(self):
        return self.name
