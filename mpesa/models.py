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
