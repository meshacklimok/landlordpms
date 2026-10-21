"""Tenant portal accounts and invitations (D-055)."""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import PublicIdModel, ScopedQuerySet, TimeStampedModel


class TenantAccount(PublicIdModel, TimeStampedModel):
    """Lets a login see one tenant record's own leases. Revoking keeps the row."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="portal_accounts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tenant_accounts")
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    objects = ScopedQuerySet.as_manager()

    class Meta:
        verbose_name = _("tenant portal account")
        constraints = [
            models.UniqueConstraint("tenant", condition=Q(revoked_at__isnull=True),
                                    name="portal_tenantaccount_one_live_per_tenant"),
        ]

    def __str__(self):
        return f"{self.user_id} → {self.tenant_id}"

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None


class PortalInvitation(PublicIdModel, TimeStampedModel):
    """An SMS link that lets the tenant with this phone claim portal access. Only the token's hash is kept."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="portal_invitations")
    phone = models.CharField(max_length=16)
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        verbose_name = _("tenant portal invitation")
        indexes = [models.Index(fields=["tenant", "created_at"])]

    def __str__(self):
        return f"Portal invite {self.phone}"

    @property
    def is_pending(self) -> bool:
        return self.accepted_at is None and self.revoked_at is None and self.expires_at > timezone.now()


class UssdPin(TimeStampedModel):
    """An optional PIN for the USSD menu, one per phone number across organizations (D-086).

    Only a keyed hash of the phone and PIN is kept. Five wrong PINs in a row lock the menu for an hour.
    """

    phone = models.CharField(max_length=20, unique=True)
    pin_hash = models.CharField(max_length=64)
    failures = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("USSD PIN")

    def __str__(self):
        return self.phone


class PortalMessage(PublicIdModel):
    """One note in the thread between a lease's tenants and the office (D-131). Each lease has one thread."""

    class Side(models.TextChoices):
        TENANT = "TENANT", _("Tenant")
        STAFF = "STAFF", _("Office")

    MAX_LENGTH = 1000

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, related_name="+")
    # The tenant record of the portal account that wrote it, or of the thread's lead tenant for an office reply.
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, related_name="+")
    side = models.CharField(max_length=6, choices=Side.choices)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    body = models.TextField(max_length=MAX_LENGTH)
    # When the other side first opened it.
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "pk"]
        indexes = [models.Index(fields=["lease", "side", "read_at"])]

    def __str__(self):
        return f"{self.side} · {self.body[:40]}"


class RenewalOffer(PublicIdModel, TimeStampedModel):
    """The office offers to renew a lease and the tenant answers in the portal (D-131).

    Accepting does not change the lease: it tells the office, who start the renewal draft from the offer.
    """

    class Status(models.TextChoices):
        OFFERED = "OFFERED", _("Offered")
        ACCEPTED = "ACCEPTED", _("Accepted")
        DECLINED = "DECLINED", _("Declined")
        WITHDRAWN = "WITHDRAWN", _("Withdrawn")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, related_name="+")
    start_date = models.DateField(_("renewal starts"))
    end_date = models.DateField(_("renewal ends"), null=True, blank=True)
    rent = models.DecimalField(_("monthly rent"), max_digits=14, decimal_places=2)
    note = models.CharField(_("note to the tenant"), max_length=500, blank=True)
    expires_on = models.DateField(_("answer by"))
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OFFERED)
    offered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    responded_at = models.DateTimeField(null=True, blank=True)
    responded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="+")
    responded_by_name = models.CharField(max_length=150, blank=True)
    # The draft the office started from an accepted offer.
    successor = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint("lease", condition=Q(status="OFFERED"), name="portal_renewaloffer_one_open"),
        ]

    def __str__(self):
        return f"Renewal offer {self.lease_id} from {self.start_date}"

    @property
    def is_open(self) -> bool:
        return self.status == self.Status.OFFERED and self.expires_on >= timezone.localdate()

    @property
    def is_expired(self) -> bool:
        return self.status == self.Status.OFFERED and self.expires_on < timezone.localdate()
