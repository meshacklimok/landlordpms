"""The message log: rules, preferences, consent, templates, announcements and every message sent (doc 11 §27, D-032, D-044).

Who receives what is decided by the catalog (code), the organization's rules, the
recipient's opt-outs and consent. Every send, skip and failure is a ``Message`` row,
which is never deleted.
"""


from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.models import AllObjectsManager, ArchivableModel, LiveManager, PublicIdModel, ScopedQuerySet, TimeStampedModel
from notifications import catalog

# Exactly one of tenant or user is set on recipient-bound rows.
_ONE_RECIPIENT = (Q(tenant__isnull=False) & Q(user__isnull=True)) | (Q(tenant__isnull=True) & Q(user__isnull=False))
# A message may also go to a bare number (an M-Pesa payer who is not a tenant).
_MESSAGE_RECIPIENT = _ONE_RECIPIENT | (Q(tenant__isnull=True) & Q(user__isnull=True) & ~Q(to=""))


class OrganizationNotificationRule(TimeStampedModel):
    """An organization's change to a catalog type. No row means the catalog default."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    type = models.CharField(_("notification"), max_length=50, choices=catalog.CHOICES)
    enabled = models.BooleanField(_("enabled"), default=True)
    channels = ArrayField(models.CharField(max_length=10, choices=catalog.CHANNEL_CHOICES), default=list,
                          help_text=_("In order of preference."))
    offsets = ArrayField(models.SmallIntegerField(), default=list, blank=True,
                         help_text=_("Reminder days relative to the date the type is about."))
    include_co_tenants = models.BooleanField(_("also send to co-tenants"), default=False)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    objects = ScopedQuerySet.as_manager()

    class Meta:
        constraints = [
            models.UniqueConstraint("organization", "type", name="notifications_rule_org_type_unique"),
        ]

    def __str__(self):
        return f"{self.organization} · {self.type}"


class OrganizationMessageSettings(TimeStampedModel):
    """What the figures in an organization's messages mean. No row means the defaults.

    The wording is edited as a template; these decide the value a field such as ``{balance}`` carries.
    """

    organization = models.OneToOneField("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    # An agreed deposit not yet paid counts as owed in {balance}. Off: rent and charges only.
    balance_includes_deposit = models.BooleanField(_("count an unpaid deposit in the balance"), default=False)
    # A tenant who paid ahead shows as a credit (-1,000.00) in {balance}. Off: the balance stops at zero.
    balance_shows_credit = models.BooleanField(_("show credit in the balance"), default=False)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    objects = ScopedQuerySet.as_manager()

    def __str__(self):
        return f"{self.organization} · message settings"


class NotificationTypeSetting(TimeStampedModel):
    """The platform owner's change to one catalog type, for every organization (set in Django admin).

    No row means the code default. Organizations still adjust on top through ``OrganizationNotificationRule``."""

    type = models.CharField(_("notification"), max_length=50, choices=catalog.CHOICES, unique=True)
    enabled = models.BooleanField(_("on by default"), default=True)
    channels = ArrayField(models.CharField(max_length=10, choices=catalog.CHANNEL_CHOICES), default=list,
                          help_text=_("In order of preference, e.g. WHATSAPP, SMS. Only channels the notification "
                                      "has wording for work; leave empty for the code default."))
    offsets = ArrayField(models.SmallIntegerField(), default=list, blank=True,
                         help_text=_("Default reminder days, for notifications that are sent relative to a date."))
    mandatory = models.BooleanField(_("required"), default=False,
                                    help_text=_("Organizations cannot switch it off and recipients cannot opt out."))
    urgent = models.BooleanField(_("urgent"), default=False, help_text=_("Sent during quiet hours instead of waiting."))
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    class Meta:
        ordering = ["type"]
        verbose_name = "notification setting"

    def __str__(self):
        return str(dict(catalog.CHOICES).get(self.type, self.type))

    def clean(self):
        from django.core.exceptions import ValidationError

        base = catalog.BY_CODENAME.get(self.type)
        if base is None:
            return
        bad = [c for c in self.channels if c not in base.channels]
        if bad:
            raise ValidationError({"channels": _("%(type)s has no wording for: %(bad)s. Available: %(ok)s.") % {
                "type": base.label, "bad": ", ".join(bad), "ok": ", ".join(base.channels)}})
        if self.offsets and not base.offsets:
            raise ValidationError({"offsets": _("This notification is not sent relative to a date.")})


class NotificationPreference(TimeStampedModel):
    """A recipient's opt-out (or opt back in) for one type, or all types, on one channel."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                             related_name="+")
    # Blank means every type.
    type = models.CharField(max_length=50, choices=catalog.CHOICES, blank=True)
    channel = models.CharField(max_length=10, choices=catalog.CHANNEL_CHOICES)
    enabled = models.BooleanField(default=False)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        constraints = [
            models.CheckConstraint(condition=_ONE_RECIPIENT, name="notifications_preference_one_recipient"),
            models.UniqueConstraint("organization", "tenant", "type", "channel", condition=Q(tenant__isnull=False),
                                    name="notifications_preference_tenant_unique"),
            models.UniqueConstraint("organization", "user", "type", "channel", condition=Q(user__isnull=False),
                                    name="notifications_preference_user_unique"),
        ]

    def __str__(self):
        return f"{self.tenant or self.user} · {self.type or '*'} · {self.channel} · {self.enabled}"


class ConsentRecord(models.Model):
    """A grant or revocation of a channel by a recipient. The latest row per channel wins."""

    class Source(models.TextChoices):
        STAFF = "STAFF", _("Recorded by staff")
        SMS_REPLY = "SMS_REPLY", _("SMS reply")
        WA_REPLY = "WA_REPLY", _("WhatsApp reply")
        PORTAL = "PORTAL", _("Tenant portal")
        IMPORT = "IMPORT", _("Import")
        EMAIL_LINK = "EMAIL_LINK", _("Email link")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                             related_name="+")
    channel = models.CharField(max_length=10, choices=catalog.CHANNEL_CHOICES)
    granted = models.BooleanField()
    source = models.CharField(max_length=10, choices=Source.choices)
    note = models.CharField(max_length=200, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=_ONE_RECIPIENT, name="notifications_consent_one_recipient"),
        ]
        indexes = [models.Index(fields=["organization", "tenant", "channel"])]

    def __str__(self):
        return f"{self.tenant or self.user} · {self.channel} · {'granted' if self.granted else 'revoked'}"


class MessageTemplate(TimeStampedModel, ArchivableModel):
    """An organization's wording for one type, channel and language, replacing the default."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    type = models.CharField(_("notification"), max_length=50, choices=catalog.CHOICES)
    channel = models.CharField(_("channel"), max_length=10, choices=catalog.CHANNEL_CHOICES)
    language = models.CharField(_("language"), max_length=2, choices=catalog.LANGUAGE_CHOICES)
    body = models.TextField(_("text"), max_length=1000)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")

    objects = LiveManager()
    all_objects = AllObjectsManager()

    class Meta:
        constraints = [
            models.UniqueConstraint("organization", "type", "channel", "language",
                                    condition=Q(archived_at__isnull=True), name="notifications_template_live_unique"),
        ]

    def __str__(self):
        return f"{self.type} · {self.channel} · {self.language}"


class Announcement(PublicIdModel):
    """A notice sent to a chosen group of tenants (D-044 item 15). Each recipient's copy is a ``Message``."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    text = models.TextField(_("text"), max_length=1000)
    # Who it went to, in words, e.g. "Riverside Court · owing 30+ days". The filters are kept in `audience`.
    summary = models.CharField(max_length=300, blank=True)
    audience = models.JSONField(default=dict, blank=True)
    recipient_count = models.PositiveIntegerField(default=0)
    urgent = models.BooleanField(default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.text[:50]


class Message(PublicIdModel):
    """One notification to one recipient: sent, waiting, failed or skipped with the reason."""

    class Status(models.TextChoices):
        QUEUED = "QUEUED", _("Queued")
        SENT = "SENT", _("Sent")
        DELIVERED = "DELIVERED", _("Delivered")
        FAILED = "FAILED", _("Failed")
        SKIPPED = "SKIPPED", _("Skipped")

    class SkipReason(models.TextChoices):
        RULE_DISABLED = "RULE_DISABLED", _("Switched off for the organization")
        OPTED_OUT = "OPTED_OUT", _("Recipient opted out")
        NO_CONSENT = "NO_CONSENT", _("No consent for the channel")
        CHANNEL_UNAVAILABLE = "CHANNEL_UNAVAILABLE", _("Channel not available")
        NO_ADDRESS = "NO_ADDRESS", _("No phone or address")
        NO_TEMPLATE = "NO_TEMPLATE", _("No text for the channel")

    MAX_ATTEMPTS = 3

    organization = models.ForeignKey("accounts.Organization", on_delete=models.PROTECT, related_name="+")
    type = models.CharField(max_length=50, choices=catalog.CHOICES)
    channel = models.CharField(max_length=10, choices=catalog.CHANNEL_CHOICES)
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                             related_name="+")
    # Snapshot of where it went: a phone, email, or blank for in-app.
    to = models.CharField(max_length=254, blank=True)
    language = models.CharField(max_length=2, choices=catalog.LANGUAGE_CHOICES, default=catalog.EN)
    # Email only (D-076).
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)
    # WhatsApp: the approved template sent, {"name", "language", "params"}, so a retry sends the same.
    provider_template = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    skip_reason = models.CharField(max_length=20, choices=SkipReason.choices, blank=True)
    error = models.CharField(max_length=300, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    send_after = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    provider = models.CharField(max_length=30, blank=True)
    provider_id = models.CharField(max_length=100, blank=True)
    cost = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    # What the message is about, for the log and for tenant history.
    lease = models.ForeignKey("leases.Lease", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    invoice = models.ForeignKey("billing.Invoice", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    payment = models.ForeignKey("payments.Payment", on_delete=models.PROTECT, null=True, blank=True,
                                related_name="+")
    announcement = models.ForeignKey(Announcement, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="messages")
    # Same key, same organization: the event was already handled (D-044 item 4).
    dedupe_key = models.CharField(max_length=150, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+")
    # In-app only: still to be pushed to the person's devices (D-089).
    push_due = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ScopedQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=_MESSAGE_RECIPIENT, name="notifications_message_one_recipient"),
            models.UniqueConstraint("organization", "dedupe_key", condition=~Q(dedupe_key=""),
                                    name="notifications_message_dedupe_unique"),
        ]
        indexes = [
            models.Index(fields=["status", "send_after"]),
            models.Index(fields=["organization", "tenant"]),
            models.Index(fields=["user", "channel", "read_at"]),
            models.Index(fields=["provider", "provider_id"]),
            models.Index(fields=["created_at"], condition=Q(push_due=True), name="notifications_message_push_due"),
        ]

    def __str__(self):
        return f"{self.type} → {self.tenant or self.user or self.to} ({self.status})"


class PushSubscription(TimeStampedModel):
    """A device where a staff member turned on alerts (D-089). The endpoint is the push service's address."""

    MAX_FAILURES = 5

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=120)
    auth = models.CharField(max_length=60)
    user_agent = models.CharField(max_length=200, blank=True)
    last_pushed_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return f"{self.user} · {self.user_agent[:40] or self.endpoint[:40]}"


class EmailSuppression(models.Model):
    """An address we stop mailing: it bounced for good, or its owner marked a message as spam. Written by the mail
    providers' webhooks (notifications/mail_hooks.py); sending skips these addresses so a dead or hostile address
    cannot keep hurting the sender reputation of everyone else."""

    class Reason(models.TextChoices):
        HARD_BOUNCE = "HARD_BOUNCE", _("Address does not exist or refuses mail")
        COMPLAINT = "COMPLAINT", _("Marked as spam by the recipient")

    email = models.EmailField(unique=True)  # lowercase
    reason = models.CharField(max_length=12, choices=Reason.choices)
    provider = models.CharField(max_length=20, blank=True)
    detail = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.email} ({self.reason})"


class PhoneOptOut(models.Model):
    """A phone number that replied STOP (or was reported opted out by the network) on a channel. Tenant opt-outs are
    kept as consent records; this covers every other person we text by bare number (owners, suppliers, prospects,
    M-Pesa payers). Written by notifications/sms_hooks.py; a START reply removes the row."""

    phone = models.CharField(max_length=20)
    channel = models.CharField(max_length=10)
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["phone", "channel"], name="phone_optout_unique")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.phone} ({self.channel})"
