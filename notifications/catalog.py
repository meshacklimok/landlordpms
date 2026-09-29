"""Notification type catalog (doc 11 §27, D-044).

Defined in code, like capabilities: the code decides when a type fires and what its
templates may say. Organizations only switch types on or off, choose channels and
timing (``OrganizationNotificationRule``) and override the wording (``MessageTemplate``).
"""

from dataclasses import dataclass, field, replace

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

# WhatsApp wording is a template Meta approved (D-044 item 16): fixed in code, never edited by an
# organization. It is the SMS wording with the organization named first and a way to stop at the end,
# so no template starts or ends with a field. [VERIFY the Swahili with a native speaker.]
WA_TEMPLATE_VERSION = 1
_WA_HEAD = {EN: "Message from {org_name}.\n\n", SW: "Ujumbe kutoka {org_name}.\n\n"}
_WA_FOOT = {EN: "\n\nReply STOP to stop WhatsApp messages.", SW: "\n\nJibu STOP kusitisha ujumbe wa WhatsApp."}


def _with_whatsapp(ntype: "NotificationType") -> "NotificationType":
    if WHATSAPP not in ntype.channels:
        return ntype
    bodies = dict(ntype.bodies)
    for (channel, language), text in ntype.bodies.items():
        if channel == SMS:
            # The payment link is SMS only: it is empty when the lease has none, and a Meta template
            # cannot have an empty field. The approved WhatsApp wording stays as it was.
            core = text.removesuffix(" {org_name}").replace(" {pay_link}", "")
            bodies[(WHATSAPP, language)] = _WA_HEAD[language] + core + _WA_FOOT[language]
    return replace(ntype, bodies=bodies)


def whatsapp_template(ntype: "NotificationType") -> str:
    """The name of the approved template for this type."""
    return f"{ntype.codename}_v{WA_TEMPLATE_VERSION}"


def whatsapp_language(ntype: "NotificationType", language: str) -> str:
    """The template language: the recipient's if the type has wording in it, else English."""
    return language if (WHATSAPP, language) in ntype.bodies else EN

_TYPES: tuple[NotificationType, ...] = (
    NotificationType(
        "invoice_issued", _("Invoice issued"), TENANT, (WHATSAPP, SMS),
        (*_TENANCY, "invoice_number", "amount", "due_date", "balance", "pay_reference", "pay_link"),
        bodies={
            (SMS, EN): "Dear {tenant_name}, invoice {invoice_number} for {unit} of {amount} is due on "
                       "{due_date}. Balance: {balance}. Pay with reference {pay_reference}. {pay_link} {org_name}",
            (SMS, SW): "Mpendwa {tenant_name}, ankara {invoice_number} ya {unit} ya {amount} inalipwa "
                       "tarehe {due_date}. Salio: {balance}. Lipa kwa kumbukumbu {pay_reference}. "
                       "{pay_link} {org_name}",
        },
    ),
    NotificationType(
        "rent_due_soon", _("Rent due soon"), TENANT, (WHATSAPP, SMS),
        (*_TENANCY, "invoice_number", "amount_due", "due_date", "pay_reference", "pay_link"),
        offsets=(3,),
        bodies={
            (SMS, EN): "Dear {tenant_name}, a reminder that {amount_due} for {unit} is due on {due_date}. "
                       "Pay with reference {pay_reference}. {pay_link} {org_name}",
            (SMS, SW): "Mpendwa {tenant_name}, tunakukumbusha kuwa {amount_due} ya {unit} inalipwa tarehe "
                       "{due_date}. Lipa kwa kumbukumbu {pay_reference}. {pay_link} {org_name}",
        },
    ),
    NotificationType(
        "rent_overdue", _("Rent overdue"), TENANT, (WHATSAPP, SMS),
        (*_TENANCY, "invoice_number", "amount_due", "due_date", "balance", "pay_reference", "pay_link"),
        offsets=(2,),
        bodies={
            (SMS, EN): "Dear {tenant_name}, {amount_due} for {unit} was due on {due_date} and is still unpaid. "
                       "Total balance: {balance}. Pay with reference {pay_reference}. {pay_link} {org_name}",
            (SMS, SW): "Mpendwa {tenant_name}, {amount_due} ya {unit} ilitakiwa kulipwa tarehe {due_date} na "
                       "bado haijalipwa. Salio lote: {balance}. Lipa kwa kumbukumbu {pay_reference}. "
                       "{pay_link} {org_name}",
        },
    ),
    NotificationType(
        "payment_received", _("Payment received"), TENANT, (WHATSAPP, SMS),
        (*_TENANCY, "amount", "paid_on", "receipt_number", "balance", "receipt_link"),
        bodies={
            (SMS, EN): "Dear {tenant_name}, we received {amount} for {unit} on {paid_on}. Receipt "
                       "{receipt_number}. Balance: {balance}. {receipt_link} {org_name}",
            (SMS, SW): "Mpendwa {tenant_name}, tumepokea {amount} ya {unit} tarehe {paid_on}. Risiti "
                       "{receipt_number}. Salio: {balance}. {receipt_link} {org_name}",
        },
    ),
    NotificationType(
        "announcement", _("Announcement"), TENANT, (WHATSAPP, SMS),
        (*_TENANCY, "text"),
        bodies={(SMS, EN): "{text} {org_name}", (SMS, SW): "{text} {org_name}"},
    ),
    NotificationType(
        "payment_pending_review", _("Payment waiting for review"), STAFF, (IN_APP,),
        ("org_name", "tenant_name", "unit", "amount", "recorded_by"),
        bodies={(IN_APP, EN): "{recorded_by} recorded {amount} from {tenant_name} ({unit}). It needs review."},
    ),
    # To whoever paid by M-Pesa when the payment could not be matched (D-045 item 7). Often not a
    # tenant, so SMS only: WhatsApp needs a grant a stranger cannot have given. [VERIFY the Swahili]
    NotificationType(
        "payment_unmatched", _("Payment not matched (to the payer)"), TENANT, (SMS,),
        ("org_name", "amount", "trans_id", "paid_on"),
        bodies={
            (SMS, EN): "We received {amount} by M-Pesa ({trans_id}) on {paid_on} but could not tell which "
                       "unit it is for. Please tell us your unit number, and use it as the account number "
                       "when you pay. {org_name}",
            (SMS, SW): "Tumepokea {amount} kwa M-Pesa ({trans_id}) tarehe {paid_on} lakini hatukujua ni ya "
                       "nyumba gani. Tafadhali tueleze nambari ya nyumba yako, na uitumie kama nambari ya "
                       "akaunti unapolipa. {org_name}",
        },
    ),
    # To a property owner's phone when their monthly statement is sent (D-058). Owners are not
    # tenants or users, so SMS only, as for the M-Pesa payer. [VERIFY the Swahili]
    NotificationType(
        "owner_statement", _("Owner statement summary (to the owner)"), TENANT, (SMS,),
        ("org_name", "owner_name", "month", "collected", "fee", "expenses", "due", "remitted", "remaining"),
        bodies={
            (SMS, EN): "Dear {owner_name}, your statement for {month}: collected {collected}, management fee "
                       "{fee}, expenses {expenses}, due to you {due}, paid to you {remitted}, still to pay "
                       "{remaining}. {org_name}",
            (SMS, SW): "Mpendwa {owner_name}, taarifa yako ya {month}: makusanyo {collected}, ada ya usimamizi "
                       "{fee}, matumizi {expenses}, kiasi chako {due}, umelipwa {remitted}, kilichobaki "
                       "{remaining}. {org_name}",
        },
    ),
    NotificationType(
        "mpesa_unmatched", _("M-Pesa payment not matched"), STAFF, (IN_APP,),
        ("org_name", "amount", "payer", "reference", "account", "trans_id"),
        bodies={(IN_APP, EN): "{amount} from {payer} (reference “{reference}”) on {account} could not be "
                              "matched. It is waiting in the M-Pesa inbox."},
    ),
    # Sent by `mpesa_daily` to organization-wide `mpesa.view_transactions` holders (D-045 item 10).
    NotificationType(
        "mpesa_daily_summary", _("M-Pesa daily summary"), STAFF, (IN_APP,),
        ("org_name", "day", "summary"),
        bodies={(IN_APP, EN): "M-Pesa on {day}. {summary}"},
    ),
    # Sent by `mpesa_daily` to `mpesa.match` holders who have codes to check (D-066 item 4).
    NotificationType(
        "mpesa_unverified_codes", _("M-Pesa codes to check"), STAFF, (IN_APP,),
        ("org_name", "count"),
        bodies={(IN_APP, EN): "{count} M-Pesa payment(s) typed in by hand were not found in what Safaricom "
                              "sent, or the amount differs. Check them under M-Pesa, Codes to check."},
    ),
)

TYPES = tuple(_with_whatsapp(t) for t in _TYPES)
BY_CODENAME: dict[str, NotificationType] = {t.codename: t for t in TYPES}
CHOICES = [(t.codename, t.label) for t in TYPES]
CHANNEL_CHOICES = [(SMS, _("SMS")), (WHATSAPP, _("WhatsApp")), (EMAIL, _("Email")), (IN_APP, _("In-app"))]
LANGUAGE_CHOICES = [(EN, _("English")), (SW, _("Swahili"))]


def get(codename: str) -> NotificationType:
    try:
        return BY_CODENAME[codename]
    except KeyError:
        raise ValueError(f"Unknown notification type {codename!r}") from None
