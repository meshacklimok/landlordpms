"""Questions asked of the assistant (D-091 item 7), by staff or by tenants on WhatsApp or in the portal (D-093, D-094),
and what the organization wants it to know (D-119).

Questions are kept 90 days, then deleted by `purge_assistant`.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import OrgScopedModel, PublicIdModel, TimeStampedModel


class Question(models.Model):
    class Status(models.TextChoices):
        ANSWERED = "ANSWERED", _("Answered")
        FAILED = "FAILED", _("Could not answer")
        LIMITED = "LIMITED", _("Over the daily limit")
        PENDING = "PENDING", _("Waiting for an answer")

    class Channel(models.TextChoices):
        STAFF = "STAFF", _("Staff, on the Ask page")
        WHATSAPP = "WHATSAPP", _("Tenant, on WhatsApp")
        PORTAL = "PORTAL", _("Tenant, in the portal")

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="+")
    # Staff questions have a user; tenants' WhatsApp questions have the tenant and the phone instead.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
                             related_name="+")
    tenant = models.ForeignKey("tenants.Tenant", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    phone = models.CharField(max_length=20, blank=True)
    channel = models.CharField(max_length=8, choices=Channel.choices, default=Channel.STAFF)
    # The WhatsApp message id: a webhook Meta sends twice is answered once.
    provider_id = models.CharField(max_length=128, blank=True)
    question = models.TextField(_("question"))
    answer = models.TextField(_("answer"), blank=True)
    status = models.CharField(_("status"), max_length=10, choices=Status.choices)
    # [{"name": ..., "input": {...}}, ...] in the order the assistant asked for them.
    tools = models.JSONField(default=list, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    # A tenant's WhatsApp proposal waiting for "YES" (D-094 item 5); "state" is open, done or ended.
    proposal = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["organization", "created_at"]), models.Index(fields=["user", "created_at"]),
                   models.Index(fields=["phone", "created_at"])]
        constraints = [
            models.UniqueConstraint(fields=["provider_id"], condition=~models.Q(provider_id=""),
                                    name="assistant_question_provider_id_unique"),
            models.CheckConstraint(condition=models.Q(user__isnull=False) | models.Q(channel="WHATSAPP"),
                                   name="assistant_question_staff_has_user"),
        ]

    def __str__(self):
        return self.question[:60]


class KnowledgeEntry(PublicIdModel, TimeStampedModel, OrgScopedModel):
    """Something an owner wants the assistant to know (D-119): office hours, deposit rules, house rules.

    The staff assistant reads every entry on the properties the member can see; the tenant assistant reads only
    entries marked for tenants, on the tenant's own properties. Entries without a property apply to all of them.
    """

    title = models.CharField(_("question or topic"), max_length=120)
    answer = models.TextField(_("answer"), max_length=1500)
    property = models.ForeignKey("properties.Property", on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="+", verbose_name=_("property"))
    for_tenants = models.BooleanField(_("tenants can be told this"), default=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["title", "pk"]
        indexes = [models.Index(fields=["organization", "for_tenants"])]

    def __str__(self):
        return self.title
