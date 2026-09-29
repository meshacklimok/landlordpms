"""Properties, buildings and units (doc 11 §5).

Three shapes share one model:
- Case A: Property ─ Units (building is null)
- Case B: Property ─ Building ─ Units
- Case C: Property ─ one "Main house" unit, created automatically for a single house

Occupancy is never stored: it comes from leases. A unit stores only its manual status.
"""

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from core.kenya import County
from core.models import AllObjectsManager, ArchivableModel, LiveManager, PublicIdModel, TimeStampedModel

# Letters and digits only, so `{PROPERTY_CODE}-{UNIT_CODE}` always splits at the first hyphen
# and two different units can never produce the same payment reference.
PROPERTY_CODE_RE = re.compile(r"^[A-Z0-9]{1,10}$")
UNIT_CODE_RE = re.compile(r"^[A-Z0-9](?:[A-Z0-9-]{0,8}[A-Z0-9])?$")

property_code_validator = RegexValidator(
    PROPERTY_CODE_RE, _("Use 1–10 letters or digits, e.g. GV.")
)
unit_code_validator = RegexValidator(
    UNIT_CODE_RE, _("Use 1–10 letters, digits or inner hyphens, e.g. A102 or B-12.")
)


def clean_code(value: str | None) -> str:
    return (value or "").strip().upper().replace(" ", "")


class PropertyOwner(PublicIdModel, TimeStampedModel):
    """Who owns one or more properties: a person or company, not a login (D-035, D-053)."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    name = models.CharField(_("name"), max_length=150)
    phone = models.CharField(_("phone"), max_length=16, blank=True)
    email = models.EmailField(_("email"), blank=True)
    note = models.CharField(_("note"), max_length=300, blank=True)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name


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
    # Short code used in payment references, e.g. GV in GV-A102 (D-029). Fixed after creation.
    code = models.CharField(_("code"), max_length=10, validators=[property_code_validator])
    category = models.CharField(_("type"), max_length=20, choices=Category.choices, default=Category.APARTMENT_BLOCK)

    # Structured Kenyan address (doc 14 D8).
    county = models.CharField(_("county"), max_length=20, choices=County.choices, blank=True)
    sub_county = models.CharField(_("sub-county / ward"), max_length=100, blank=True)
    area = models.CharField(_("estate / area"), max_length=100, blank=True)
    street = models.CharField(_("street / landmark"), max_length=200, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    # Agency office that runs this property (D-040): design-in only, same organization.
    branch = models.ForeignKey("accounts.Branch", on_delete=models.PROTECT, null=True, blank=True,
                               related_name="properties")

    # Who owns it and the agency's fee on rent collected (D-053).
    owner = models.ForeignKey(PropertyOwner, on_delete=models.PROTECT, null=True, blank=True,
                              related_name="properties", verbose_name=_("owner"))
    management_fee_percent = models.DecimalField(
        _("management fee (%)"), max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text=_("Taken from rent collected on the owner statement. Leave empty for no fee."))
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

    def clean(self):
        if self.branch_id and self.branch.organization_id != self.organization_id:
            raise ValidationError({"branch": _("Choose a branch of this organization.")})
        if self.owner_id and self.owner.organization_id != self.organization_id:
            raise ValidationError({"owner": _("Choose an owner of this organization.")})

    def save(self, *args, **kwargs):
        self.code = clean_code(self.code)
        super().save(*args, **kwargs)

    @property
    def address_line(self) -> str:
        parts = [self.street, self.area, self.sub_county, self.get_county_display() if self.county else ""]
        return ", ".join(p for p in parts if p)


class Building(PublicIdModel, TimeStampedModel, ArchivableModel):
    """Optional grouping inside a large property (Case B)."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="buildings")
    name = models.CharField(_("name"), max_length=100)

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint("property", Upper("name"), name="properties_building_property_name_unique"),
        ]

    def __str__(self):
        return self.name


class Unit(PublicIdModel, TimeStampedModel, ArchivableModel):
    class Type(models.TextChoices):
        APARTMENT = "APARTMENT", _("Apartment")
        HOUSE = "HOUSE", _("House")
        BEDSITTER = "BEDSITTER", _("Bedsitter")
        STUDIO = "STUDIO", _("Studio")
        SHOP = "SHOP", _("Shop")
        OFFICE = "OFFICE", _("Office")
        WAREHOUSE = "WAREHOUSE", _("Warehouse")
        PARKING = "PARKING", _("Parking")
        # Hostels: design-in only (D-040). Room/bed assignment and semester billing come later.
        BED_SPACE = "BED_SPACE", _("Bed space")
        OTHER = "OTHER", _("Other")

    class ManualStatus(models.TextChoices):
        NORMAL = "NORMAL", _("Normal")
        RESERVED = "RESERVED", _("Reserved")
        UNDER_MAINTENANCE = "UNDER_MAINTENANCE", _("Under maintenance")
        INACTIVE = "INACTIVE", _("Inactive")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="units")
    # Must belong to the same property; checked in services.
    building = models.ForeignKey(Building, on_delete=models.PROTECT, null=True, blank=True, related_name="units")
    code = models.CharField(_("unit code"), max_length=10, validators=[unit_code_validator])
    unit_type = models.CharField(_("type"), max_length=20, choices=Type.choices, default=Type.APARTMENT)
    type_label = models.CharField(_("label"), max_length=60, blank=True, help_text=_("Optional, e.g. 2 bedroom"))
    manual_status = models.CharField(
        _("status"), max_length=20, choices=ManualStatus.choices, default=ManualStatus.NORMAL
    )
    # `{PROPERTY_CODE}-{UNIT_CODE}`: the account number a tenant types on Paybill (D-029).
    payment_reference = models.CharField(max_length=21, editable=False)
    # Asking rent, used for vacancy-loss reporting. The lease holds the real rent.
    list_rent = models.DecimalField(
        _("asking rent"), max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )

    # Public vacancy link /v/<token>/ (D-040). Null when not shared; sharing again issues a new token.
    share_token = models.CharField(max_length=32, null=True, blank=True, unique=True, editable=False)  # noqa: DJ001
    # Whose name and phone the public page shows as the contact.
    shared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        ordering = ["property__name", "code"]
        constraints = [
            # Both hold across archived rows (doc 11 §23).
            models.UniqueConstraint("property", Upper("code"), name="properties_unit_property_code_unique"),
            models.UniqueConstraint(
                "organization", Upper("payment_reference"), name="properties_unit_org_payment_reference_unique"
            ),
        ]
        indexes = [models.Index(fields=["organization", "manual_status"])]

    def __str__(self):
        return f"{self.property.name} · {self.code}"

    def save(self, *args, **kwargs):
        self.code = clean_code(self.code)
        super().save(*args, **kwargs)

    # A method, not @property: the `property` field shadows the builtin in this class body.
    def get_effective_status_display(self) -> str:
        """Shown status: Occupied while an active lease has started, else the manual status.

        Lists annotate `is_occupied` (leases.services.with_occupancy) to avoid a query per unit.
        """
        occupied = getattr(self, "is_occupied", None)
        if occupied is None:
            from leases.services import occupying_leases

            occupied = occupying_leases().filter(unit=self).exists()
        if occupied:
            return _("Occupied")
        if self.manual_status == self.ManualStatus.NORMAL:
            return _("Available")
        return self.get_manual_status_display()


def payment_reference_for(property_code: str, unit_code: str) -> str:
    return f"{clean_code(property_code)}-{clean_code(unit_code)}"
