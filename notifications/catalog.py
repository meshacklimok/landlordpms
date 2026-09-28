"""Notification type catalog (doc 11 §27, D-044).

Defined in code, like capabilities: the code decides when a type fires and what its
templates may say. Organizations only switch types on or off, choose channels and
timing (``OrganizationNotificationRule``) and override the wording (``MessageTemplate``).
"""

from dataclasses import dataclass, field

from django.utils.translation import gettext_lazy as _

SMS = "SMS"
WHATSAPP = "WHATSAPP"
EMAIL = "EMAIL"
IN_APP = "IN_APP"

TENANT = "tenant"
STAFF = "staff"

EN = "en"
SW = "sw"
LANGUAGES = (EN, SW)


@dataclass(frozen=True)
class NotificationType:
    codename: str
    label: str
    audience: str
    channels: tuple[str, ...]
    placeholders: tuple[str, ...]
    # Default bodies: {(channel, language): text}. A channel with no body cannot be used.
    bodies: dict[tuple[str, str], str] = field(default_factory=dict)
    enabled: bool = True
    # Cannot be switched off by an organization or opted out of by a recipient.
    mandatory: bool = False
    # Sent during quiet hours instead of waiting.
    urgent: bool = False
    # Reminder offsets in days, for types a daily job fires relative to a date.
    offsets: tuple[int, ...] = ()

    def default_body(self, channel: str, language: str) -> str | None:
        return self.bodies.get((channel, language)) or self.bodies.get((channel, EN))


_TENANCY = ("tenant_name", "org_name", "unit", "property")

TYPES: tuple[NotificationType, ...] = (
    NotificationType(
        "invoice_issued", _("Invoice issued"), TENANT, (SMS,),
        (*_TENANCY, "invoice_number", "amount", "due_date", "balance", "pay_reference"),
        bodies={
            (SMS, EN): "Dear {tenant_name}, invoice {invoice_number} for {unit} of {amount} is due on "
                       "{due_date}. Balance: {balance}. Pay with reference {pay_reference}. {org_name}",
            (SMS, SW): "Mpendwa {tenant_name}, ankara {invoice_number} ya {unit} ya {amount} inalipwa "
                       "tarehe {due_date}. Salio: {balance}. Lipa kwa kumbukumbu {pay_reference}. {org_name}",
        },
    ),
    NotificationType(
        "rent_due_soon", _("Rent due soon"), TENANT, (SMS,),
        (*_TENANCY, "invoice_number", "amount_due", "due_date", "pay_reference"),
        offsets=(3,),
        bodies={
            (SMS, EN): "Dear {tenant_name}, a reminder that {amount_due} for {unit} is due on {due_date}. "
                       "Pay with reference {pay_reference}. {org_name}",
            (SMS, SW): "Mpendwa {tenant_name}, tunakukumbusha kuwa {amount_due} ya {unit} inalipwa tarehe "
                       "{due_date}. Lipa kwa kumbukumbu {pay_reference}. {org_name}",
        },
    ),
    NotificationType(
        "rent_overdue", _("Rent overdue"), TENANT, (SMS,),
        (*_TENANCY, "invoice_number", "amount_due", "due_date", "balance", "pay_reference"),
        offsets=(2,),
        bodies={
            (SMS, EN): "Dear {tenant_name}, {amount_due} for {unit} was due on {due_date} and is still unpaid. "
                       "Total balance: {balance}. Pay with reference {pay_reference}. {org_name}",
            (SMS, SW): "Mpendwa {tenant_name}, {amount_due} ya {unit} ilitakiwa kulipwa tarehe {due_date} na "
                       "bado haijalipwa. Salio lote: {balance}. Lipa kwa kumbukumbu {pay_reference}. {org_name}",
        },
    ),
    NotificationType(
        "payment_received", _("Payment received"), TENANT, (SMS,),
        (*_TENANCY, "amount", "paid_on", "receipt_number", "balance", "receipt_link"),
        bodies={
            (SMS, EN): "Dear {tenant_name}, we received {amount} for {unit} on {paid_on}. Receipt "
                       "{receipt_number}. Balance: {balance}. {receipt_link} {org_name}",
            (SMS, SW): "Mpendwa {tenant_name}, tumepokea {amount} ya {unit} tarehe {paid_on}. Risiti "
                       "{receipt_number}. Salio: {balance}. {receipt_link} {org_name}",
        },
    ),
    NotificationType(
        "announcement", _("Announcement"), TENANT, (SMS,),
        (*_TENANCY, "text"),
        bodies={(SMS, EN): "{text} {org_name}", (SMS, SW): "{text} {org_name}"},
    ),
    NotificationType(
        "payment_pending_review", _("Payment waiting for review"), STAFF, (IN_APP,),
        ("org_name", "tenant_name", "unit", "amount", "recorded_by"),
        bodies={(IN_APP, EN): "{recorded_by} recorded {amount} from {tenant_name} ({unit}). It needs review."},
    ),
)

BY_CODENAME: dict[str, NotificationType] = {t.codename: t for t in TYPES}
CHOICES = [(t.codename, t.label) for t in TYPES]
CHANNEL_CHOICES = [(SMS, _("SMS")), (WHATSAPP, _("WhatsApp")), (EMAIL, _("Email")), (IN_APP, _("In-app"))]
LANGUAGE_CHOICES = [(EN, _("English")), (SW, _("Swahili"))]


def get(codename: str) -> NotificationType:
    try:
        return BY_CODENAME[codename]
    except KeyError:
        raise ValueError(f"Unknown notification type {codename!r}") from None
