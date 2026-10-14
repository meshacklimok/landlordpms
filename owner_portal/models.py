"""Owner portal accounts and invitations (D-128).

A PropertyOwner is a record, not a login. An OwnerAccount links one login to one owner record so the owner
can see their own statements and payments; like the tenant portal (D-055), revoking keeps the row.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import PublicIdModel, ScopedQuerySet, TimeStampedModel


class OwnerAccount(PublicIdModel, TimeStampedModel):
    """Lets a login see one owner record's statements, payments and properties."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    owner = models.ForeignKey("properties.PropertyOwner", on_delete=models.PROTECT, related_name="portal_accounts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owner_accounts")
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    objects = ScopedQuerySet.as_manager()

    class Meta:
        verbose_name = _("owner portal account")
        constraints = [
            models.UniqueConstraint("owner", condition=Q(revoked_at__isnull=True),
                                    name="owner_portal_account_one_live_per_owner"),
        ]

    def __str__(self):
        return f"{self.user_id} → owner {self.owner_id}"

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None


class OwnerInvitation(PublicIdModel, TimeStampedModel):
    """A link sent by SMS and/or email. Claiming it needs a login with the invited, verified phone.
    Only the token's hash is kept."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    owner = models.ForeignKey("properties.PropertyOwner", on_delete=models.PROTECT, related_name="portal_invitations")
    phone = models.CharField(max_length=16)
    email = models.EmailField(blank=True)
    sent_by_sms = models.BooleanField(default=False)
    sent_by_email = models.BooleanField(default=False)
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        verbose_name = _("owner portal invitation")
        indexes = [models.Index(fields=["owner", "created_at"])]

    def __str__(self):
        return f"Owner portal invite {self.phone}"

    @property
    def is_pending(self) -> bool:
        return self.accepted_at is None and self.revoked_at is None and self.expires_at > timezone.now()
