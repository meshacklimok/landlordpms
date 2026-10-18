"""Identity and access: User, Organization, Membership, roles and capabilities.

User ≠ Organization ≠ Role (doc 11 §0, D-014). Roles are per-organization copies
of platform templates and fully editable (doc 13, D-023, D-024).
"""

import datetime
import os

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.files.storage import FileSystemStorage
from django.core.mail import send_mail
from django.core.validators import MaxValueValidator, RegexValidator
from django.db import models
from django.db.models.functions import Lower, Upper
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext_lazy as _

from core.models import (
    AllObjectsManager,
    ArchivableModel,
    LiveManager,
    PublicIdModel,
    TimeStampedModel,
)
from core.phone import normalize_phone
from notifications.providers.mail import SYSTEM, mailer_for

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
    # Raised to end every other session, e.g. when MFA is switched on or off (D-059).
    session_epoch = models.PositiveIntegerField(default=0, editable=False)
    # The version of the Terms of Service and Privacy Policy accepted, and when (D-095).
    terms_version = models.CharField(max_length=20, blank=True, default="", editable=False)
    terms_accepted_at = models.DateTimeField(null=True, blank=True, editable=False)

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

    def _get_session_auth_hash(self, secret=None):
        """Django's hash of the password, with the epoch added once it is raised, so sessions made
        before any raise stay valid."""
        value = self.password if not self.session_epoch else f"{self.password}:{self.session_epoch}"
        key_salt = "django.contrib.auth.models.AbstractBaseUser.get_session_auth_hash"
        return salted_hmac(key_salt, value, secret=secret, algorithm="sha256").hexdigest()

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.phone

    def email_user(self, subject, message, from_email=None, **kwargs):
        """No-op when the user has no email (email is optional)."""
        if self.email:
            kwargs.setdefault("using", mailer_for(SYSTEM))
            send_mail(subject, message, from_email, [self.email], **kwargs)


class TOTPDevice(models.Model):
    """A user's authenticator app (D-059). Not in use until a code has been checked (confirmed_at)."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="totp")
    # Base32 secret, encrypted with core.crypto.
    secret = models.TextField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # The last 30-second step accepted, so a code cannot be used twice.
    last_step = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"TOTP {self.user}"

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None


class RecoveryCode(models.Model):
    """A single-use code for when the phone is lost. Stored hashed; shown once (D-059)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recovery_codes")
    code_hash = models.CharField(max_length=64)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Recovery code {self.user}"


class OTPCode(models.Model):
    """One-time code sent by SMS (or by email for VERIFY_EMAIL, still keyed by the phone). Temporary: may be hard-deleted (doc 11 §23)."""

    class Purpose(models.TextChoices):
        VERIFY_PHONE = "VERIFY_PHONE", _("Verify phone")
        RESET_PASSWORD = "RESET_PASSWORD", _("Reset password")
        VERIFY_EMAIL = "VERIFY_EMAIL", _("Verify email")

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

    class SenderStatus(models.TextChoices):
        NONE = "", _("Platform name")
        REQUESTED = "REQUESTED", _("Requested")
        APPROVED = "APPROVED", _("Approved")
        REJECTED = "REJECTED", _("Rejected")

    class Type(models.TextChoices):
        INDIVIDUAL = "INDIVIDUAL", _("Individual landlord")
        COMPANY = "COMPANY", _("Company")
        AGENCY = "AGENCY", _("Property management agency")
        # Not landlords (D-130): they bill households monthly, with their own words for people and places.
        ESTATE = "ESTATE", _("Estate or residents' association")
        WATER = "WATER", _("Water vendor")
        WASTE = "WASTE", _("Garbage collection company")

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", _("Active")
        # Lapsed subscription: data visible and exportable, nothing new (doc 14 A12).
        READ_ONLY = "READ_ONLY", _("Read-only")
        # Frozen by the Platform Admin for abuse or security (doc 14 D14).
        FROZEN = "FROZEN", _("Frozen")

    class LateFeeMode(models.TextChoices):
        OFF = "OFF", _("No late fees")
        FIXED = "FIXED", _("A fixed amount")
        PERCENT = "PERCENT", _("A percentage of what is unpaid")

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
    # Estates, water vendors and garbage collectors choose whether they hold deposits (D-130). Landlords always do.
    holds_deposits = models.BooleanField(_("we hold deposits"), default=False)
    # How the owner wants deposits cleared (D-150). Kenyan law sets no refund deadline, so the lease and these rules do.
    deposit_refund_days = models.PositiveSmallIntegerField(
        _("days to settle a deposit"), default=30, validators=[MaxValueValidator(365)],
        help_text=_("Days after a lease ends by which the deposit is refunded or a statement of deductions is given."))
    deposit_allow_offset = models.BooleanField(_("deposit may be put towards rent owed"), default=True)
    deposit_proof_required = models.BooleanField(_("deductions need proof"), default=False)
    # Codes from billing.DepositEntry.Heading a deduction may use; empty means any, and the heading is optional.
    deposit_deduction_headings = models.JSONField(_("deduction headings"), default=list, blank=True)
    # Late fees (D-129): off until the owner picks a way to work them out. Staff approve each fee before it is charged.
    late_fee_mode = models.CharField(_("late fees"), max_length=8, choices=LateFeeMode.choices,
                                     default=LateFeeMode.OFF)
    late_fee_amount = models.DecimalField(_("late fee amount"), max_digits=14, decimal_places=2, null=True,
                                          blank=True)
    late_fee_percent = models.DecimalField(_("late fee percent"), max_digits=5, decimal_places=2, null=True,
                                           blank=True)
    # Most a percentage fee may come to on one invoice; blank means no limit.
    late_fee_cap = models.DecimalField(_("late fee maximum"), max_digits=14, decimal_places=2, null=True,
                                       blank=True)
    # Set when late fees are switched on: invoices overdue before this day never get one.
    late_fees_from = models.DateField(_("late fees from"), null=True, blank=True, editable=False)
    # Notifications (doc 11 §27, D-044): non-urgent messages wait until quiet hours end.
    quiet_hours_start = models.TimeField(_("quiet hours start"), default=datetime.time(21, 0))
    quiet_hours_end = models.TimeField(_("quiet hours end"), default=datetime.time(7, 0))
    # Replies to emails sent to tenants go here (D-076). Blank: no reply-to.
    reply_email = models.EmailField(_("reply-to email"), blank=True)
    # The assistant answers staff questions from the organization's figures (D-091). Off until an owner turns it on.
    assistant_enabled = models.BooleanField(_("assistant on"), default=False)
    # Tenants' questions on WhatsApp (D-093), a separate switch from the staff one.
    tenant_assistant_enabled = models.BooleanField(_("tenant assistant on"), default=False)
    # Photos read a month (D-144). Blank: the platform's SCAN_MONTHLY_LIMIT; 0 turns scans off for this landlord.
    scan_limit = models.PositiveIntegerField(_("photo scans a month"), null=True, blank=True)
    # Every member must pass two-step login before opening the organization (D-107). Tenants in the portal are not
    # members, so this never reaches them.
    require_mfa = models.BooleanField(_("two-step login for all staff"), default=False)
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
    # What the invoice PDF adds (D-149): the owner's address under the name, how to pay, terms, and the tenant's contacts.
    address = models.CharField(_("address"), max_length=200, blank=True)
    invoice_payment_info = models.TextField(_("how to pay"), max_length=500, blank=True)
    invoice_terms = models.TextField(_("invoice terms"), max_length=600, blank=True)
    invoice_show_tenant_contact = models.BooleanField(_("tenant phone and email on invoices"), default=False)
    # Printed above "Authorised signature" at the end of the invoice.
    stamp = models.ImageField(_("signature or stamp"), upload_to="org-stamps/", blank=True)
    invoice_show_previous_balance = models.BooleanField(_("earlier unpaid invoices on invoices"), default=True)
    # Own SMS sender name (D-120), a paid add-on: the owner asks, a Platform Admin registers it with the networks
    # and approves it. Until then, and after a rejection, tenant SMS go under the platform's AT_SENDER_ID.
    sms_sender_id = models.CharField(_("SMS sender name"), max_length=11, blank=True)
    sms_sender_status = models.CharField(_("SMS sender name status"), max_length=10, choices=SenderStatus.choices,
                                         blank=True, default=SenderStatus.NONE)
    sms_sender_note = models.CharField(_("note to the organization"), max_length=200, blank=True,
                                       help_text=_("Shown on their Subscription page, e.g. why it was rejected."))
    sms_sender_requested_at = models.DateTimeField(null=True, blank=True)
    sms_sender_decided_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    def __str__(self):
        return self.name

    @property
    def display_name(self) -> str:
        return self.brand_name or self.name

    @property
    def vocab(self) -> dict:
        """What this organization calls tenants, units and the rest (D-130)."""
        from .vocabulary import vocabulary_for

        return vocabulary_for(self.org_type)

    @property
    def bills_rent(self) -> bool:
        """False for estates, water vendors and garbage collectors, whose messages avoid rent words (D-130)."""
        return self.org_type not in (self.Type.ESTATE, self.Type.WATER, self.Type.WASTE)

    @property
    def takes_deposits(self) -> bool:
        """Whether deposits are held and cleared (D-130): always for landlords, by the owner's choice for the rest."""
        return self.bills_rent or self.holds_deposits

    @property
    def sms_sender_name(self) -> str:
        """The sender name its SMS go out under, or blank for the platform's."""
        return self.sms_sender_id if self.sms_sender_status == self.SenderStatus.APPROVED else ""

    @property
    def is_operational(self) -> bool:
        return self.status == self.Status.ACTIVE and not self.is_archived


class Branch(PublicIdModel, TimeStampedModel, ArchivableModel):
    """An agency's office (D-040). Staff given a branch see every property in it (D-127)."""

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
        default=False, help_text=_("If off, only properties listed under property access and in the branches below.")
    )
    # Every property in these branches, now and later (D-127). Archived branches give nothing.
    branches = models.ManyToManyField(Branch, blank=True, related_name="memberships")
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
    branches = models.ManyToManyField(Branch, blank=True, related_name="+")
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


class _ExportStorage(FileSystemStorage):
    """Files under DATA_EXPORT_DIR, read each time so a changed setting (or a test's) is followed."""

    @property
    def base_location(self):
        return str(settings.DATA_EXPORT_DIR)

    @property
    def location(self):
        return os.path.abspath(self.base_location)


def _export_storage():
    return _ExportStorage()


class DataExport(PublicIdModel, TimeStampedModel):
    """One organization's data as a ZIP, built by the run_data_exports job (D-177).

    The file is kept outside media, downloaded only by the person who asked while signed in, and deleted when it
    expires. ``for_console`` marks an export the platform owner asked for from the console.
    """

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Waiting")
        RUNNING = "RUNNING", _("Being made")
        READY = "READY", _("Ready")
        FAILED = "FAILED", _("Failed")
        EXPIRED = "EXPIRED", _("Expired")

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="data_exports")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    for_console = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    file = models.FileField(storage=_export_storage, upload_to="", blank=True)
    size = models.PositiveBigIntegerField(default=0)
    rows = models.PositiveIntegerField(default=0)
    files = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    downloads = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self):
        return f"Data export of {self.organization} ({self.get_status_display()})"

    @property
    def is_open(self) -> bool:
        return self.status in (self.Status.PENDING, self.Status.RUNNING)

    @property
    def can_download(self) -> bool:
        return self.status == self.Status.READY and bool(self.file) and self.expires_at > timezone.now()
