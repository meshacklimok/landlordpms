"""Organization API keys (D-082). Only the SHA-256 of the secret is stored; the key is shown once."""

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import PublicIdModel, TimeStampedModel


class ApiKey(PublicIdModel, TimeStampedModel):
    class Scope(models.TextChoices):
        PROPERTIES = "properties", _("Properties and units")
        TENANTS = "tenants", _("Tenants")
        LEASES = "leases", _("Leases")
        INVOICES = "invoices", _("Invoices")
        PAYMENTS = "payments", _("Payments")
        WRITE_PAYMENTS = "write_payments", _("Record payments")
        WRITE_TENANTS = "write_tenants", _("Add and update tenants")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    membership = models.ForeignKey("accounts.Membership", on_delete=models.PROTECT, related_name="api_keys",
                                   help_text=_("The key reads with this staff member's rights."))
    name = models.CharField(_("name"), max_length=60)
    prefix = models.CharField(max_length=8, unique=True, editable=False)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    scopes = models.JSONField(default=list)
    expires_at = models.DateTimeField(_("expires"), null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True, editable=False)
    revoked_at = models.DateTimeField(null=True, blank=True, editable=False)
    revoked_by = models.ForeignKey("accounts.User", on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+", editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["organization", "revoked_at"])]

    def __str__(self):
        return f"{self.name} (lpk_{self.prefix}_…)"

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None and not self.is_expired

    def scope_labels(self) -> list[str]:
        labels = dict(self.Scope.choices)
        return [str(labels[s]) for s in self.scopes if s in labels]

    @classmethod
    def live_q(cls) -> Q:
        return Q(revoked_at__isnull=True) & (Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))


# Scopes that change data, and the read scope each one brings (D-087 item 2).
WRITE_SCOPES = {ApiKey.Scope.WRITE_PAYMENTS: ApiKey.Scope.PAYMENTS, ApiKey.Scope.WRITE_TENANTS: ApiKey.Scope.TENANTS}


class IdempotentRequest(TimeStampedModel):
    """The first successful answer to a write sent with an Idempotency-Key, kept 24 hours (D-087 item 3)."""

    api_key = models.ForeignKey(ApiKey, on_delete=models.CASCADE, related_name="+")
    key = models.CharField(max_length=100)
    request_hash = models.CharField(max_length=64)
    status = models.PositiveSmallIntegerField()
    body = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint("api_key", "key", name="api_idempotentrequest_key_unique")]

    def __str__(self):
        return self.key


class WebhookEndpoint(PublicIdModel, TimeStampedModel):
    """Where to POST events (D-083). Sends with its maker's rights, like an API key."""

    class Event(models.TextChoices):
        PAYMENT_CONFIRMED = "payment.confirmed", _("Payment confirmed")
        PAYMENT_REVERSED = "payment.reversed", _("Payment reversed")
        INVOICE_ISSUED = "invoice.issued", _("Invoice issued")
        INVOICE_VOIDED = "invoice.voided", _("Invoice voided")
        LEASE_ACTIVATED = "lease.activated", _("Lease activated")
        LEASE_ENDED = "lease.ended", _("Lease ended")
        TENANT_CREATED = "tenant.created", _("Tenant added")
        TENANT_UPDATED = "tenant.updated", _("Tenant updated")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    membership = models.ForeignKey("accounts.Membership", on_delete=models.PROTECT, related_name="webhooks")
    url = models.URLField(_("URL"), max_length=300)
    events = models.JSONField(default=list)
    secret_encrypted = models.TextField(editable=False)
    last_audit_id = models.BigIntegerField(default=0, editable=False)
    paused_at = models.DateTimeField(null=True, blank=True, editable=False)
    pause_reason = models.CharField(max_length=200, blank=True, editable=False)
    consecutive_failures = models.PositiveIntegerField(default=0, editable=False)
    last_success_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self):
        return self.url

    @property
    def is_paused(self) -> bool:
        return self.paused_at is not None

    def event_labels(self) -> list[str]:
        labels = dict(self.Event.choices)
        return [str(labels[e]) for e in self.events if e in labels]


class WebhookDelivery(PublicIdModel, TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "PENDING", _("Waiting")
        SENT = "SENT", _("Delivered")
        FAILED = "FAILED", _("Failed")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    endpoint = models.ForeignKey(WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries")
    event = models.CharField(max_length=40)
    audit_event = models.ForeignKey("audit.AuditEvent", on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+")
    payload = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    response_excerpt = models.CharField(max_length=1024, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint("endpoint", "audit_event", name="api_webhookdelivery_endpoint_event_unique"),
        ]
        indexes = [models.Index(fields=["status", "next_attempt_at"])]

    def __str__(self):
        return f"{self.event} → {self.endpoint_id}"
