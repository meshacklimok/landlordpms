"""Signing the tenancy agreement and the move-out statement electronically (D-118).

A request freezes one PDF and its SHA-256 when it is sent. Each tenant signs through a private link after a
code sent to their phone; the landlord signs in the app. When all have signed, a signing record page is
added after the original pages and the signed copy is filed with the lease's documents.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import PublicIdModel, TimeStampedModel


class SigningRequest(PublicIdModel, TimeStampedModel):
    class Kind(models.TextChoices):
        AGREEMENT = "AGREEMENT", _("Tenancy agreement")
        MOVE_OUT = "MOVE_OUT", _("Move-out statement")

    class Status(models.TextChoices):
        PENDING = "PENDING", _("Waiting for signatures")
        COMPLETED = "COMPLETED", _("Signed by everyone")
        DECLINED = "DECLINED", _("Declined")
        CANCELLED = "CANCELLED", _("Cancelled")
        EXPIRED = "EXPIRED", _("Expired")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, related_name="signing_requests")
    kind = models.CharField(_("document"), max_length=10, choices=Kind.choices)
    number = models.CharField(_("number"), max_length=30, editable=False)
    title = models.CharField(_("title"), max_length=150)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices, default=Status.PENDING)
    # What is signed, frozen when the request is made.
    original_pdf = models.FileField(upload_to="esign/%Y/", editable=False)
    original_sha256 = models.CharField(max_length=64, editable=False)
    # The original with the signing record added, once everyone has signed.
    signed_pdf = models.FileField(upload_to="esign/%Y/", blank=True, editable=False)
    signed_sha256 = models.CharField(max_length=64, blank=True, editable=False)
    expires_at = models.DateTimeField()
    share_with_tenant = models.BooleanField(default=False)
    # The sender confirmed the agreement is a short-term lease (Land Act 2012 s.58): two years or less with no
    # option to renew, or periodic. Longer leases are signed on paper before a witness instead.
    no_renewal_option = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    completed_at = models.DateTimeField(null=True, blank=True)
    # The signed copy in the lease's documents.
    document = models.ForeignKey("documents.Document", on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+")
    close_reason = models.CharField(max_length=300, blank=True)
    # Opens /s/<code>/ without a login, to check a copy.
    verify_code = models.CharField(max_length=32, unique=True, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint("organization", "number", name="esign_request_org_number_unique"),
            models.UniqueConstraint("lease", "kind", condition=Q(status="PENDING"),
                                    name="esign_request_one_pending_per_kind"),
            models.CheckConstraint(condition=~Q(status="COMPLETED") | ~Q(signed_sha256=""),
                                   name="esign_request_completed_has_signed_copy"),
            models.CheckConstraint(condition=~Q(status__in=["CANCELLED", "DECLINED"]) | ~Q(close_reason=""),
                                   name="esign_request_closed_has_reason"),
            models.CheckConstraint(condition=~Q(kind="AGREEMENT") | Q(no_renewal_option=True),
                                   name="esign_request_agreement_is_short_lease"),
        ]

    def __str__(self):
        return self.number

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.PENDING and self.expires_at > timezone.now()

    @property
    def shown_status(self) -> str:
        """The status as people see it: a waiting request past its day reads as expired."""
        if self.status == self.Status.PENDING and not self.is_open:
            return self.Status.EXPIRED.label
        return self.get_status_display()


class Signer(PublicIdModel, TimeStampedModel):
    class Role(models.TextChoices):
        LANDLORD = "LANDLORD", _("For the landlord")
        TENANT = "TENANT", _("Tenant")

    class Via(models.TextChoices):
        LINK = "LINK", _("Private link and SMS code")
        PORTAL = "PORTAL", _("Signed in to the tenant portal")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    request = models.ForeignKey(SigningRequest, on_delete=models.PROTECT, related_name="signers")
    role = models.CharField(max_length=10, choices=Role.choices)
    position = models.PositiveSmallIntegerField(default=0)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    # As they were when the request was made; the landlord's name is the organization's.
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=16, blank=True)

    # The private link: only its SHA-256 is kept.
    token_hash = models.CharField(max_length=64, blank=True, db_index=True)
    link_sent_at = models.DateTimeField(null=True, blank=True)
    # The code sent to the phone, as an HMAC.
    code_hash = models.CharField(max_length=64, blank=True)
    code_sent_at = models.DateTimeField(null=True, blank=True)
    code_expires_at = models.DateTimeField(null=True, blank=True)
    code_attempts = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)
    # How the tenant was checked: the private link and SMS code, or their signed-in portal account.
    signed_via = models.CharField(max_length=6, choices=Via.choices, default=Via.LINK)

    signed_at = models.DateTimeField(null=True, blank=True)
    signed_name = models.CharField(max_length=200, blank=True)
    # The staff member who signed for the landlord.
    signed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+")
    signed_ip = models.GenericIPAddressField(null=True, blank=True)
    signed_user_agent = models.CharField(max_length=300, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)
    decline_reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(role="LANDLORD") | Q(tenant__isnull=False, phone__gt=""),
                                   name="esign_signer_tenant_has_phone"),
            models.CheckConstraint(condition=Q(signed_at__isnull=True) | Q(declined_at__isnull=True),
                                   name="esign_signer_signed_or_declined"),
            models.CheckConstraint(condition=Q(signed_at__isnull=True) | ~Q(signed_name=""),
                                   name="esign_signer_signed_has_name"),
            models.CheckConstraint(condition=Q(declined_at__isnull=True) | ~Q(decline_reason=""),
                                   name="esign_signer_declined_has_reason"),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_role_display()})"

    @property
    def has_signed(self) -> bool:
        return self.signed_at is not None
