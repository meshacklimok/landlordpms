"""M-Pesa through each organization's own Daraja app (doc 11 §10, D-045).

`DarajaCredentials` holds one Paybill or Till account's Daraja keys, encrypted at rest, and the
secret token in its callback URLs. The keys are never shown back to anyone.
"""

import secrets

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core import crypto
from core.models import ScopedQuerySet, TimeStampedModel

# Daraja refuses callback URLs containing these words, in any case. [VERIFY the full list]
FORBIDDEN_URL_WORDS = ("mpesa", "m-pesa", "safaricom", "exe", "exec", "cmd", "sql", "query")


def new_callback_token() -> str:
    """A random token that never contains a word Daraja refuses."""
    while True:
        token = secrets.token_urlsafe(32)
        if not any(word in token.lower() for word in FORBIDDEN_URL_WORDS):
            return token


class DarajaCredentials(TimeStampedModel):
    """The Daraja app behind one M-Pesa payment account."""

    class Environment(models.TextChoices):
        SANDBOX = "SANDBOX", _("Sandbox (testing)")
        PRODUCTION = "PRODUCTION", _("Live")

    SECRETS = ("consumer_key", "consumer_secret", "passkey")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    payment_account = models.OneToOneField("payments.PaymentAccount", on_delete=models.PROTECT,
                                           related_name="daraja")
    environment = models.CharField(_("environment"), max_length=10, choices=Environment.choices,
                                   default=Environment.SANDBOX)
    # The shortcode registered with Daraja: the Paybill number, or a Till's store number. [VERIFY for Tills]
    shortcode = models.CharField(_("shortcode"), max_length=10)
    consumer_key_encrypted = models.TextField(blank=True)
    consumer_secret_encrypted = models.TextField(blank=True)
    passkey_encrypted = models.TextField(blank=True)
    # Secret part of this account's callback URLs; a new one needs the URLs registered again.
    callback_token = models.CharField(max_length=64, unique=True, default=new_callback_token, editable=False)
    urls_registered_at = models.DateTimeField(null=True, blank=True)
    registration_error = models.CharField(max_length=300, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    objects = ScopedQuerySet.as_manager()

    class Meta:
        verbose_name_plural = "Daraja credentials"

    def __str__(self):
        return f"{self.payment_account} · {self.shortcode}"

    def secret(self, name: str) -> str:
        return crypto.decrypt(getattr(self, f"{name}_encrypted"))

    def set_secret(self, name: str, value: str) -> None:
        setattr(self, f"{name}_encrypted", crypto.encrypt(value))

    def has(self, name: str) -> bool:
        return bool(getattr(self, f"{name}_encrypted"))

    @property
    def can_connect(self) -> bool:
        return self.has("consumer_key") and self.has("consumer_secret")

    @property
    def can_request_payment(self) -> bool:
        """STK push needs a Paybill and the passkey (D-045 item 8)."""
        return self.can_connect and self.has("passkey") and self.payment_account.type == "PAYBILL"


class MpesaTransaction(TimeStampedModel):
    """One payment Safaricom told us about, kept exactly as received (D-045 items 4-6).

    Stored first, processed after: a RECEIVED row that failed to process is retried later. Never
    deleted; a payment that is not rent is IGNORED with a reason.
    """

    class Source(models.TextChoices):
        C2B = "C2B", _("Paybill / Till")
        STK = "STK", _("Payment request")

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED", _("Received")
        MATCHED = "MATCHED", _("Matched")
        UNMATCHED = "UNMATCHED", _("Waiting to be matched")
        FLAGGED = "FLAGGED", _("Flagged")
        IGNORED = "IGNORED", _("Ignored")

    class MatchedBy(models.TextChoices):
        REFERENCE = "REFERENCE", _("Account reference")
        MANUAL = "MANUAL", _("By hand")
        STK = "STK", _("Payment request")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    payment_account = models.ForeignKey("payments.PaymentAccount", on_delete=models.PROTECT,
                                        related_name="mpesa_transactions")
    source = models.CharField(max_length=3, choices=Source.choices, default=Source.C2B)
    # Safaricom's receipt number, e.g. QAB12CD34E. A repeated callback finds the existing row.
    trans_id = models.CharField(_("M-Pesa code"), max_length=20, unique=True)
    trans_type = models.CharField(max_length=40, blank=True)
    shortcode = models.CharField(max_length=10, blank=True)
    # The account number the payer typed, as typed.
    bill_ref = models.CharField(_("account reference"), max_length=60, blank=True)
    amount = models.DecimalField(_("amount"), max_digits=14, decimal_places=2)
    paid_at = models.DateTimeField(_("paid at"))
    # MSISDN as received: a number, or a SHA-256 hash of it (D-045 item 5).
    msisdn_raw = models.CharField(max_length=80, blank=True)
    msisdn_hash = models.CharField(max_length=64, blank=True, db_index=True)
    # E.164, only when the real number arrived; only then can the payer be texted.
    payer_phone = models.CharField(max_length=16, blank=True)
    payer_name = models.CharField(max_length=150, blank=True)
    raw_payload = models.JSONField(default=dict)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RECEIVED)
    # Why it was flagged, ignored or left unmatched, or how the suggestion was found.
    note = models.CharField(max_length=300, blank=True)
    # A phone match is only a suggestion for the inbox; it never confirms anything.
    suggested_lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, null=True, blank=True,
                                        related_name="+")
    payment = models.OneToOneField("payments.Payment", on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="mpesa_transaction")
    matched_by = models.CharField(max_length=10, choices=MatchedBy.choices, blank=True)
    matched_by_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                                        blank=True, related_name="+")
    matched_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-paid_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gte=0), name="mpesa_transaction_amount_not_negative"),
            models.CheckConstraint(condition=~models.Q(status="MATCHED") | models.Q(payment__isnull=False),
                                   name="mpesa_transaction_matched_has_payment"),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["payment_account", "paid_at"]),
        ]

    def __str__(self):
        return self.trans_id
