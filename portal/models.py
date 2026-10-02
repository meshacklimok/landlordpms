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
