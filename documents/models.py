"""Documents: lease agreements, ID copies, title deeds, permits and house rules (D-071).

- A document belongs to one place: a property, a unit, a lease or a tenant. Unit and lease documents
  also record their property, so they are scoped by property like everything else.
- The file is private and served only through a view that checks scope. It is never deleted: archive it.
"""

import builtins
import datetime

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import ArchivableModel, PublicIdModel, TimeStampedModel

EXPIRING_DAYS = 30


class Document(PublicIdModel, TimeStampedModel, ArchivableModel):
    class Category(models.TextChoices):
        LEASE = "LEASE", _("Lease agreement")
        ID = "ID", _("ID or passport copy")
        KRA = "KRA", _("KRA PIN certificate")
        TITLE = "TITLE", _("Title deed or ownership")
        INSURANCE = "INSURANCE", _("Insurance")
        PERMIT = "PERMIT", _("Licence or permit")
        RULES = "RULES", _("House rules")
        NOTICE = "NOTICE", _("Notice or letter")
        BILL = "BILL", _("Utility bill")
        CONTRACT = "CONTRACT", _("Contract")
        PHOTO = "PHOTO", _("Photo")
        OTHER = "OTHER", _("Other")

    class Kind(models.TextChoices):
        PDF = "PDF", "PDF"
        PHOTO = "PHOTO", _("Photo")
        WORD = "WORD", "Word"
        EXCEL = "EXCEL", "Excel"

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    # Set for property, unit and lease documents; empty for tenant documents.
    property = models.ForeignKey("properties.Property", on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="documents", editable=False)
    unit = models.ForeignKey("properties.Unit", on_delete=models.PROTECT, null=True, blank=True,
                             related_name="documents", editable=False)
    lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, null=True, blank=True,
                              related_name="documents", editable=False)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, null=True, blank=True,
                               related_name="documents", editable=False)
    title = models.CharField(_("title"), max_length=120)
    category = models.CharField(_("category"), max_length=10, choices=Category.choices, default=Category.OTHER)
    file = models.FileField(upload_to="documents/%Y/", editable=False)
    kind = models.CharField(max_length=5, choices=Kind.choices, editable=False)
    filename = models.CharField(max_length=200, editable=False)
    size = models.PositiveIntegerField(default=0, editable=False)
    expires_on = models.DateField(_("expires on"), null=True, blank=True)
    note = models.CharField(_("note"), max_length=300, blank=True)
    shared_with_tenant = models.BooleanField(_("shared with the tenant"), default=False, editable=False)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            # A tenant document has no property; anything else has one, and at most one of unit or lease.
            models.CheckConstraint(
                condition=(Q(tenant__isnull=False, property__isnull=True, unit__isnull=True, lease__isnull=True)
                           | (Q(tenant__isnull=True, property__isnull=False)
                              & ~Q(unit__isnull=False, lease__isnull=False))),
                name="documents_document_one_place"),
            models.CheckConstraint(condition=Q(shared_with_tenant=False) | Q(unit__isnull=True, tenant__isnull=True),
                                   name="documents_document_shareable"),
        ]
        indexes = [models.Index(fields=["organization", "archived_at", "expires_on"])]

    def __str__(self):
        return self.title

    @builtins.property
    def place(self) -> str:
        if self.tenant_id:
            return "tenant"
        if self.lease_id:
            return "lease"
        if self.unit_id:
            return "unit"
        return "property"

    @builtins.property
    def shareable(self) -> bool:
        return self.place in ("lease", "property")

    @builtins.property
    def is_photo(self) -> bool:
        return self.kind == self.Kind.PHOTO

    def expiry_state(self, today: datetime.date | None = None) -> str:
        """"expired", "expiring" (within 30 days) or ""."""
        if self.expires_on is None:
            return ""
        today = today or timezone.localdate()
        if self.expires_on < today:
            return "expired"
        return "expiring" if self.expires_on <= today + datetime.timedelta(days=EXPIRING_DAYS) else ""
