"""Identity and access: User, Organization, Membership, roles and capabilities.

User ≠ Organization ≠ Role (doc 11 §0, D-014). Roles are per-organization copies
of platform templates and fully editable (doc 13, D-023, D-024).
"""

import datetime

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.mail import send_mail
from django.core.validators import MaxValueValidator, RegexValidator
from django.db import models
from django.db.models.functions import Lower, Upper
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import (
    AllObjectsManager,
    ArchivableModel,
    LiveManager,
    PublicIdModel,
    TimeStampedModel,
)
from core.phone import normalize_phone

# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, phone, password, **extra):
        phone = normalize_phone(phone)
        email = extra.pop("email", None)
        email = self.normalize_email(email).lower() if email else None
        user = self.model(phone=phone, email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, phone, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(phone, password, **extra)

    def create_superuser(self, phone, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if not extra["is_staff"] or not extra["is_superuser"]:
            raise ValueError("Superuser must have is_staff=True and is_superuser=True.")
        return self._create_user(phone, password, **extra)

    def get_by_natural_key(self, phone):
        return self.get(phone=normalize_phone(phone))


class User(PublicIdModel, AbstractBaseUser, PermissionsMixin):
    """A person who logs in. Phone first, email optional (doc 14 A11, D-036).

    Platform Admins are ``is_staff``/``is_superuser``; they are not a role inside
    any organization.
    """

    phone = models.CharField(_("phone"), max_length=16, unique=True, help_text=_("E.164, e.g. +254712345678"))
    # Nullable so uniqueness applies only to users who have an email.
    email = models.EmailField(_("email"), null=True, blank=True)  # noqa: DJ001
    full_name = models.CharField(_("full name"), max_length=150)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    # Start of the SIM-swap cool-down after an OTP password reset (doc 14 A11).
    password_reset_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text=_("Platform Admin: can use Django admin."))
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="accounts_user_email_unique_ci",
                condition=models.Q(email__isnull=False),
            ),
        ]

    def __str__(self):
        return self.full_name or self.phone

    def clean(self):
        super().clean()
        if self.phone:
            self.phone = normalize_phone(self.phone)
        if self.email:
            self.email = self.__class__.objects.normalize_email(self.email).lower()
        else:
            self.email = None

    @property
    def phone_verified(self) -> bool:
        return self.phone_verified_at is not None

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.phone

    def email_user(self, subject, message, from_email=None, **kwargs):
        """No-op when the user has no email (email is optional)."""
        if self.email:
            send_mail(subject, message, from_email, [self.email], **kwargs)


class OTPCode(models.Model):
    """One-time code sent by SMS. Temporary: may be hard-deleted (doc 11 §23)."""

    class Purpose(models.TextChoices):
        VERIFY_PHONE = "VERIFY_PHONE", _("Verify phone")
        RESET_PASSWORD = "RESET_PASSWORD", _("Reset password")

    phone = models.CharField(max_length=16, db_index=True)
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["phone", "purpose", "-created_at"])]

    def __str__(self):
        return f"{self.purpose} {self.phone}"


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------


class Organization(PublicIdModel, TimeStampedModel, ArchivableModel):
    """The workspace that owns business data (the customer, D-038)."""

    class Type(models.TextChoices):
        INDIVIDUAL = "INDIVIDUAL", _("Individual landlord")
        COMPANY = "COMPANY", _("Company")
        AGENCY = "AGENCY", _("Property management agency")

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", _("Active")
        # Lapsed subscription: data visible and exportable, nothing new (doc 14 A12).
        READ_ONLY = "READ_ONLY", _("Read-only")
        # Frozen by the Platform Admin for abuse or security (doc 14 D14).
        FROZEN = "FROZEN", _("Frozen")

    class TaxResidence(models.TextChoices):
        RESIDENT = "RESIDENT", _("Resident in Kenya")
        NON_RESIDENT = "NON_RESIDENT", _("Non-resident")

    name =models.CharField(_("name"), max_length=150)
    org_type = models.CharField(_("type"), max_length=20, choices=Type.choices, default=Type.INDIVIDUAL)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    currency = models.CharField(max_length=3, default="KES")
    # Tax and billing settings (D-034): design-in only, no calculation yet.
    kra_pin = models.CharField(_("KRA PIN"), max_length=20, blank=True)
    vat_registered = models.BooleanField(default=False)
    billing_email = models.EmailField(blank=True)
    billing_phone = models.CharField(max_length=16, blank=True)
    # Picks the rate in the rental income tax estimate (D-050). An estimate only, never filed.
    landlord_tax_residence = models.CharField(
        _("landlord tax residence"), max_length=12, choices=TaxResidence.choices, default=TaxResidence.RESIDENT)
    # Rent billing (doc 14 A3, D-036). Partial first and last months are prorated by days
    # unless the organization bills them as a full month.
    prorate_partial_months = models.BooleanField(_("prorate partial months"), default=True)
    invoice_lead_days = models.PositiveSmallIntegerField(
        _("invoice lead days"), default=5, validators=[MaxValueValidator(28)],
        help_text=_("How many days before the month starts its invoices go out."))
    # Notifications (doc 11 §27, D-044): non-urgent messages wait until quiet hours end.
    quiet_hours_start = models.TimeField(_("quiet hours start"), default=datetime.time(21, 0))
    quiet_hours_end = models.TimeField(_("quiet hours end"), default=datetime.time(7, 0))
    # What tenancy letters state besides the tenants, units and dates (D-048).
    letter_show_payment_record = models.BooleanField(_("on-time payment record"), default=True)
    letter_show_balance = models.BooleanField(_("balance owed"), default=True)
    letter_show_deposit = models.BooleanField(_("deposit status"), default=True)
    letter_show_rent = models.BooleanField(_("monthly rent"), default=True)
    # Branding for agencies and white-label (D-040): design-in only. Receipts, statements and
    # public pages will use these; there is no settings page yet.
    brand_name = models.CharField(_("brand name"), max_length=150, blank=True,
                                  help_text=_("Shown to tenants instead of the organization name."))
    logo = models.ImageField(_("logo"), upload_to="org-logos/", blank=True)
    brand_color = models.CharField(_("brand colour"), max_length=7, blank=True, validators=[
        RegexValidator(r"^#[0-9A-Fa-f]{6}$", _("Use a hex colour like #1A73E8."))])
    document_footer = models.CharField(_("document footer"), max_length=200, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    def __str__(self):
        return self.name

    @property
    def display_name(self) -> str:
        return self.brand_name or self.name

    @property
    def is_operational(self) -> bool:
        return self.status == self.Status.ACTIVE and not self.is_archived


class Branch(PublicIdModel, TimeStampedModel, ArchivableModel):
    """An agency's office (D-040): design-in only. Properties may point at one; nothing scopes by it yet."""

    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="branches")
    name = models.CharField(_("name"), max_length=100)
    phone = models.CharField(_("phone"), max_length=16, blank=True)
    email = models.EmailField(_("email"), blank=True)

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "branches"
        constraints = [
            models.UniqueConstraint("organization", Upper("name"), name="accounts_branch_org_name_unique"),
        ]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Capabilities and roles
# ---------------------------------------------------------------------------


class Capability(models.Model):
    """Mirror of ``accounts.capabilities.CAPABILITIES``; synced, never hand-made."""

    codename = models.CharField(max_length=64, unique=True)
    module = models.CharField(max_length=32, db_index=True)
    description = models.CharField(max_length=200)
    sensitive = models.BooleanField(default=False)
    org_wide = models.BooleanField(default=False)
    read_only_safe = models.BooleanField(default=False)
    # False when a capability was removed from code; kept for history.
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["module", "codename"]
        verbose_name_plural = "capabilities"

    def __str__(self):
        return self.codename


class RoleTemplate(TimeStampedModel):
    """Platform default role. Edited by the Platform Admin; affects new organizations only."""

    key = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=255, blank=True)
    capabilities = models.ManyToManyField(Capability, blank=True, related_name="templates")
    is_owner_template = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Role(PublicIdModel, TimeStampedModel, ArchivableModel):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="roles")
    name = models.CharField(_("name"), max_length=80)
    description = models.CharField(max_length=255, blank=True)
    based_on_template = models.ForeignKey(
        RoleTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name="roles"
    )
    is_owner_role = models.BooleanField(default=False)
    capabilities = models.ManyToManyField(Capability, through="RoleCapability", blank=True, related_name="roles")

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                "organization", Lower("name"), name="accounts_role_org_name_unique_ci"
            ),
        ]

    def __str__(self):
        return self.name


class RoleCapability(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE)
    capability = models.ForeignKey(Capability, on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["role", "capability"], name="accounts_rolecap_unique"),
        ]

    def __str__(self):
        return f"{self.role_id}:{self.capability_id}"


# ---------------------------------------------------------------------------
# Membership, overrides, property scope, invitations
# ---------------------------------------------------------------------------


class Membership(PublicIdModel, TimeStampedModel, ArchivableModel):
    """A user's seat in one organization: one role, plus overrides and property scope."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="memberships")
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="memberships")
    all_properties = models.BooleanField(
        default=False, help_text=_("If off, only properties listed under property access.")
    )
    # Suspended when False; archived when removed from the organization.
    is_active = models.BooleanField(default=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "organization"], name="accounts_membership_unique"),
        ]

    def __str__(self):
        return f"{self.user} @ {self.organization} ({self.role})"


class MembershipCapability(models.Model):
    """Per-person override: grant (True) or withhold (False) one capability."""

    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="overrides")
    capability = models.ForeignKey(Capability, on_delete=models.PROTECT)
    granted = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["membership", "capability"], name="accounts_membercap_unique"),
        ]

    def __str__(self):
        return f"{'+' if self.granted else '-'}{self.capability}"


class PropertyAccess(models.Model):
    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="property_access")
    property = models.ForeignKey("properties.Property", on_delete=models.PROTECT, related_name="access")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "property access"
        constraints = [
            models.UniqueConstraint(fields=["membership", "property"], name="accounts_propaccess_unique"),
        ]

    def __str__(self):
        return f"{self.membership_id} → {self.property_id}"


class Invitation(PublicIdModel, TimeStampedModel):
    """Staff invite by SMS/email link. Expired invites may be hard-deleted."""

    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="invitations")
    phone = models.CharField(max_length=16)
    email = models.EmailField(blank=True)
    full_name = models.CharField(max_length=150, blank=True)
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="invitations")
    all_properties = models.BooleanField(default=False)
    properties = models.ManyToManyField("properties.Property", blank=True, related_name="+")
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Invite {self.phone} to {self.organization}"

    @property
    def is_pending(self) -> bool:
        return self.accepted_at is None and self.revoked_at is None and self.expires_at > timezone.now()
