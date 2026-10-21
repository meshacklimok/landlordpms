"""The accounts messages go out through: the platform's or an organization's own WhatsApp, email and SMS provider (D-120).

Provider secrets are kept encrypted and never shown back.
"""

import secrets

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core import crypto
from core.models import ScopedQuerySet, TimeStampedModel


def new_webhook_token() -> str:
    return secrets.token_urlsafe(24)


def new_verify_token() -> str:
    return secrets.token_urlsafe(18)


class SecretsMixin(models.Model):
    """Provider secrets kept encrypted in `<name>_encrypted` columns. Never shown back to anyone."""

    SECRETS: tuple[str, ...] = ()

    class Meta:
        abstract = True

    def secret(self, name: str) -> str:
        return crypto.decrypt(getattr(self, f"{name}_encrypted"))

    def set_secret(self, name: str, value: str) -> None:
        setattr(self, f"{name}_encrypted", crypto.encrypt(value))

    def has(self, name: str) -> bool:
        return bool(getattr(self, f"{name}_encrypted"))


class WhatsAppAccount(SecretsMixin, TimeStampedModel):
    """An organization's own WhatsApp Business number on Meta's Cloud API.

    The access token and app secret are encrypted at rest and never shown back. The two random values belong to
    the organization's own webhook in Meta: the token in its URL, and the verify token Meta asks for once.
    """

    SECRETS = ("access_token", "app_secret")

    organization = models.OneToOneField("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    phone_number_id = models.CharField(_("phone number ID"), max_length=30, unique=True)
    display_number = models.CharField(max_length=40, blank=True)
    verified_name = models.CharField(max_length=120, blank=True)
    access_token_encrypted = models.TextField(blank=True)
    app_secret_encrypted = models.TextField(blank=True)
    enabled = models.BooleanField(_("use this number"), default=True)
    webhook_token = models.CharField(max_length=64, unique=True, default=new_webhook_token,
                                     editable=False)
    verify_token = models.CharField(max_length=64, default=new_verify_token, editable=False)
    checked_at = models.DateTimeField(null=True, blank=True)
    check_error = models.CharField(max_length=300, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    objects = ScopedQuerySet.as_manager()

    def __str__(self):
        return f"{self.organization} · {self.phone_number_id}"

    @property
    def usable(self) -> bool:
        """It can send: switched on, with a number and a token."""
        return self.enabled and bool(self.phone_number_id) and self.has("access_token")


class PlatformWhatsApp(SecretsMixin, TimeStampedModel):
    """The platform owner's own WhatsApp number, set in Django admin. One row.

    It serves every organization that has no number of its own, and replaces the WA_* settings when it is
    switched on and complete."""

    SECRETS = ("access_token", "app_secret")

    phone_number_id = models.CharField(_("phone number ID"), max_length=30, blank=True)
    access_token_encrypted = models.TextField(blank=True)
    app_secret_encrypted = models.TextField(blank=True)
    verify_token = models.CharField(max_length=64, default=new_verify_token)
    enabled = models.BooleanField(_("use this number"), default=False)

    class Meta:
        verbose_name = "platform WhatsApp number"
        verbose_name_plural = "platform WhatsApp number"

    def __str__(self):
        return f"Platform WhatsApp {self.phone_number_id}".strip()

    @classmethod
    def get(cls) -> "PlatformWhatsApp":
        return cls.objects.get_or_create(pk=1)[0]

    @property
    def usable(self) -> bool:
        return self.enabled and bool(self.phone_number_id) and self.has("access_token")


class EmailAccount(SecretsMixin, TimeStampedModel):
    """An organization's own outgoing mail server, so tenant mail comes from its own address.

    Optional: without one, mail goes through the platform's account under the organization's name."""

    SECRETS = ("password",)

    organization = models.OneToOneField("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    host = models.CharField(_("mail server"), max_length=200)
    port = models.PositiveIntegerField(_("port"), default=587)
    username = models.CharField(_("username"), max_length=200, blank=True)
    password_encrypted = models.TextField(blank=True)
    use_tls = models.BooleanField(_("use TLS"), default=True)
    from_email = models.EmailField(_("send from"))
    from_name = models.CharField(_("sender name"), max_length=100, blank=True)
    enabled = models.BooleanField(_("use this mail server"), default=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    check_error = models.CharField(max_length=300, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    objects = ScopedQuerySet.as_manager()

    def __str__(self):
        return f"{self.organization} · {self.from_email}"

    @property
    def usable(self) -> bool:
        return self.enabled and bool(self.host) and bool(self.from_email)


class SmsAccount(SecretsMixin, TimeStampedModel):
    """An organization's own Africa's Talking account, so its SMS are paid for and sent by it, under its own name.

    Optional: without one, SMS go through the platform's account and the organization's SMS wallet."""

    SECRETS = ("api_key",)

    organization = models.OneToOneField("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    username = models.CharField(_("Africa's Talking username"), max_length=60)
    api_key_encrypted = models.TextField(blank=True)
    sender_id = models.CharField(_("sender name"), max_length=11, blank=True)
    enabled = models.BooleanField(_("use this account"), default=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    check_error = models.CharField(max_length=300, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    objects = ScopedQuerySet.as_manager()

    def __str__(self):
        return f"{self.organization} · {self.username}"

    @property
    def usable(self) -> bool:
        return self.enabled and bool(self.username) and self.has("api_key")
