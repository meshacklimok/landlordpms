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
    # Email subject lines by language, filled like the body (D-076). Fixed in code.
    subjects: dict[str, str] = field(default_factory=dict)
    # Written in full by staff for one tenant (D-125): no organization rule, no editable wording.
    composed: bool = False

    def default_body(self, channel: str, language: str) -> str | None:
        return self.bodies.get((channel, language)) or self.bodies.get((channel, EN))

    def subject(self, language: str) -> str:
        return self.subjects.get(language) or self.subjects.get(EN) or str(self.label)


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
        (*_TENANCY, "text", "pay_ref"),
        bodies={(SMS, EN): "{text} {org_name}", (SMS, SW): "{text} {org_name}"},
    ),
    # One message a staff member wrote for one tenant, on the channel they chose (D-125). WhatsApp goes
    # with the approved announcement template, so this type has no template of its own.
    NotificationType(
        "tenant_message", _("Message from staff"), TENANT, (SMS, EMAIL),
        ("org_name", "text"),
        bodies={(SMS, EN): "{text} {org_name}", (EMAIL, EN): "{text}"},
        composed=True,
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
    # Maintenance (D-068 item 8). [VERIFY the Swahili]
    NotificationType(
        "maintenance_update", _("Maintenance update (to the tenant)"), TENANT, (WHATSAPP, SMS),
        (*_TENANCY, "number", "title", "status", "note"),
        bodies={
            (SMS, EN): "Dear {tenant_name}, your repair request {number} ({title}) for {unit}: {status}. {note} "
                       "{org_name}",
            (SMS, SW): "Mpendwa {tenant_name}, ombi lako la matengenezo {number} ({title}) la {unit}: {status}. "
                       "{note} {org_name}",
        },
    ),
    # To a supplier's phone when a job is assigned to them. Suppliers are not tenants or users, so SMS only.
    # D-074 item 8: with the respond-by and fix-by times. [VERIFY the Swahili]
    NotificationType(
        "maintenance_supplier_job", _("Maintenance job (to the supplier)"), TENANT, (SMS,),
        ("org_name", "supplier_name", "number", "property", "unit", "title", "priority", "assigned_by",
         "respond_by", "due"),
        bodies={
            (SMS, EN): "Hello {supplier_name}, job {number} at {property}, {unit}: {title}. Priority: {priority}. "
                       "Please start by {respond_by} and finish by {due}. Assigned by {assigned_by}. {org_name}",
            (SMS, SW): "Habari {supplier_name}, kazi {number} katika {property}, {unit}: {title}. Kipaumbele: "
                       "{priority}. Tafadhali anza kabla ya {respond_by} na umalize kabla ya {due}. Imetolewa na "
                       "{assigned_by}. {org_name}",
        },
    ),
    NotificationType(
        "maintenance_reported", _("Maintenance request reported"), STAFF, (IN_APP,),
        ("org_name", "number", "property", "unit", "title", "priority", "reported_by"),
        bodies={(IN_APP, EN): "{number} ({priority}) at {property}, {unit}: {title}. Reported by {reported_by}. "
                              "It needs to be assigned."},
    ),
    NotificationType(
        "maintenance_assigned", _("Maintenance job assigned"), STAFF, (IN_APP,),
        ("org_name", "number", "property", "unit", "title", "priority", "due", "assigned_by"),
        bodies={(IN_APP, EN): "{assigned_by} assigned you {number} ({priority}) at {property}, {unit}: {title}. "
                              "Due by {due}."},
    ),
    # D-074 item 7: sent by `maintenance_alerts` when a job passes its respond-by or fix-by time.
    NotificationType(
        "maintenance_late", _("Maintenance job late"), STAFF, (IN_APP,),
        ("org_name", "number", "property", "unit", "title", "priority", "what", "due"),
        bodies={(IN_APP, EN): "{number} ({priority}) at {property}, {unit}: {title} is {what}. It was due by {due}."},
    ),
    # D-072 item 7: an enquiry from a vacancy link, to whoever shared it or to `prospects.manage` holders.
    NotificationType(
        "prospect_new", _("New enquiry about a vacancy"), STAFF, (IN_APP,),
        ("org_name", "name", "phone", "property", "unit", "source"),
        bodies={(IN_APP, EN): "{name} ({phone}) asked about {property}, {unit} ({source}). Call them back soon."},
    ),
    NotificationType(
        "prospect_assigned", _("Prospect assigned"), STAFF, (IN_APP,),
        ("org_name", "name", "phone", "property", "unit", "assigned_by"),
        bodies={(IN_APP, EN): "{assigned_by} gave you prospect {name} ({phone}) for {property}, {unit}."},
    ),
    NotificationType(
        "prospect_viewing", _("Viewing booked"), STAFF, (IN_APP,),
        ("org_name", "name", "phone", "property", "unit", "when", "booked_by"),
        bodies={(IN_APP, EN): "{booked_by} booked you to show {property}, {unit} to {name} ({phone}) on {when}."},
    ),
    # Notices to vacate (D-106). The tenant hears back when they gave notice; a notice from the landlord
    # is served as a letter (D-109), not by SMS. [VERIFY the Swahili]
    NotificationType(
        "notice_received", _("Notice to vacate received (to the tenant)"), TENANT, (WHATSAPP, SMS),
        (*_TENANCY, "move_out_on", "balance"),
        bodies={
            (SMS, EN): "Dear {tenant_name}, we have your notice to vacate {unit}. Your move-out day is {move_out_on}. "
                       "Balance: {balance}. We will call you to agree the move-out inspection. {org_name}",
            (SMS, SW): "Mpendwa {tenant_name}, tumepokea notisi yako ya kuhama {unit}. Siku yako ya kuhama ni "
                       "{move_out_on}. Salio: {balance}. Tutakupigia kupanga ukaguzi wa kuhama. {org_name}",
        },
    ),
    NotificationType(
        "move_out_soon", _("Move-out day coming (to the tenant)"), TENANT, (WHATSAPP, SMS),
        (*_TENANCY, "move_out_on", "balance"),
        offsets=(7,),
        bodies={
            (SMS, EN): "Dear {tenant_name}, a reminder that you move out of {unit} on {move_out_on}. Balance: "
                       "{balance}. Please agree a time for the move-out inspection and handing back the keys. "
                       "{org_name}",
            (SMS, SW): "Mpendwa {tenant_name}, tunakukumbusha kuwa unahama {unit} tarehe {move_out_on}. Salio: "
                       "{balance}. Tafadhali panga muda wa ukaguzi wa kuhama na kurudisha funguo. {org_name}",
        },
    ),
    NotificationType(
        "notice_given", _("Notice to vacate recorded"), STAFF, (IN_APP,),
        ("org_name", "tenant_name", "property", "unit", "given_by", "move_out_on", "recorded_by"),
        bodies={(IN_APP, EN): "Notice to vacate for {property}, {unit} ({tenant_name}), given by {given_by}: "
                              "move out on {move_out_on}. Recorded by {recorded_by}."},
    ),
    # The portal's messages and renewal offers (D-131).
    NotificationType(
        "portal_message_received", _("Message from a tenant in the portal"), STAFF, (IN_APP,),
        ("org_name", "tenant_name", "property", "unit"),
        bodies={(IN_APP, EN): "{tenant_name} ({property}, {unit}) wrote to you in the portal. Open Conversations "
                              "to read and reply."},
    ),
    NotificationType(
        "portal_message_reply", _("Reply from the office (to the tenant)"), TENANT, (SMS,),
        _TENANCY,
        bodies={
            (SMS, EN): "Dear {tenant_name}, {org_name} replied to your message. Open your portal to read it.",
            (SMS, SW): "Mpendwa {tenant_name}, {org_name} wamejibu ujumbe wako. Fungua tovuti yako kusoma.",
        },
    ),
    NotificationType(
        "renewal_offered", _("Renewal offer (to the tenant)"), TENANT, (SMS,),
        (*_TENANCY, "start_date", "rent", "expires_on"),
        bodies={
            (SMS, EN): "Dear {tenant_name}, {org_name} offers to renew your lease of {unit} from {start_date} at "
                       "{rent}. Open your portal to accept or decline by {expires_on}.",
            (SMS, SW): "Mpendwa {tenant_name}, {org_name} wanakupa kuongeza muda wa upangaji wa {unit} kuanzia "
                       "{start_date} kwa {rent}. Fungua tovuti yako kukubali au kukataa kabla ya {expires_on}.",
        },
    ),
    NotificationType(
        "renewal_answered", _("Renewal offer answered (to staff)"), STAFF, (IN_APP,),
        ("org_name", "tenant_name", "property", "unit", "answer"),
        bodies={(IN_APP, EN): "{tenant_name} ({property}, {unit}) {answer} the renewal offer. Open the lease to "
                              "carry on."},
    ),
    # Sent by `mpesa_daily` to `mpesa.match` holders who have codes to check (D-066 item 4).
    NotificationType(
        "mpesa_unverified_codes", _("M-Pesa codes to check"), STAFF, (IN_APP,),
        ("org_name", "count"),
        bodies={(IN_APP, EN): "{count} M-Pesa payment(s) typed in by hand were not found in what Safaricom "
                              "sent, or the amount differs. Check them under M-Pesa, Codes to check."},
    ),
    # Sent by `mpesa_daily` to `mpesa.settings` holders whose Paybill or Till setup has stalled (D-174).
    NotificationType(
        "mpesa_setup_stalled", _("Paybill setup not finished"), STAFF, (IN_APP,),
        ("org_name", "account", "step"),
        bodies={(IN_APP, EN): "{account} is not connected yet. Next step: {step}. Open M-Pesa settings to "
                              "carry on, or message us on WhatsApp and we will help."},
    ),
    # Sent from the daily job in the last days of the month to `meters.record` holders (D-155).
    NotificationType(
        "meters_unread", _("Meters not read yet"), STAFF, (IN_APP,),
        ("org_name", "count", "property"),
        bodies={(IN_APP, EN): "{count} meter(s) at {property} have no reading this month yet. Tenants are billed "
                              "from the readings, so read them before the month ends under Meters, Read meters."},
    ),
    # To whoever sent a document for e-signature (D-118).
    NotificationType(
        "esign_completed", _("Document signed by everyone"), STAFF, (IN_APP,),
        ("org_name", "number", "title"),
        bodies={(IN_APP, EN): "{title} ({number}) is signed by everyone. The signed copy is in the lease's "
                              "documents."},
    ),
    NotificationType(
        "esign_declined", _("Document declined"), STAFF, (IN_APP,),
        ("org_name", "number", "title", "signer", "reason"),
        bodies={(IN_APP, EN): "{signer} declined to sign {title} ({number}): {reason}"},
    ),
    # To those who manage the organization when someone changes who may do what, or where money and data go
    # (D-108 item 2). The riskiest also go by SMS, from accounts.alerts. Cannot be switched off.
    NotificationType(
        "security_change", _("Security change"), STAFF, (IN_APP,),
        ("org_name", "actor", "what", "when"),
        bodies={(IN_APP, EN): "Security: {actor} {what} on {when}. If you did not expect this, check Staff and "
                              "Settings now."},
        mandatory=True, urgent=True,
    ),
    # To whoever manages the plan, when a referral pays off (D-133). Never names the other business.
    NotificationType(
        "referral_rewarded", _("Referral reward earned"), STAFF, (IN_APP,),
        ("org_name", "reward"),
        bodies={(IN_APP, EN): "A landlord you referred is set up, so you earned {reward}. See Refer a landlord for "
                              "your referrals."},
    ),
    NotificationType(
        "referral_waiting", _("Referral waiting for your first payment"), STAFF, (IN_APP,),
        ("org_name", "reward"),
        bodies={(IN_APP, EN): "A landlord you referred is set up. Pay for a plan once and you earn {reward}."},
    ),
    NotificationType(
        "referral_welcome", _("Referral reward for joining"), STAFF, (IN_APP,),
        ("org_name", "reward"),
        bodies={(IN_APP, EN): "Because you joined with a referral, you got {reward}. Thank you for trying "
                              "NyumbaPMS."},
    ),
)

# Email copies to tenants (D-076): {codename: (subjects, bodies)}. Email is a copy sent as well as the
# phone channel, never a fallback, so it goes after the phone channels. Line breaks are kept.
# [VERIFY the Swahili with a native speaker.]
_PAY = {EN: "Pay with account number {pay_reference}. {pay_link}",
        SW: "Lipa kwa nambari ya akaunti {pay_reference}. {pay_link}"}
_PAID = {EN: "If you have already paid, thank you, and please ignore this email.",
         SW: "Ikiwa umeshalipa, asante, na tafadhali puuza barua pepe hii."}
_EMAIL: dict[str, tuple[dict[str, str], dict[str, str]]] = {
    "invoice_issued": (
        {EN: "Invoice {invoice_number} from {org_name}", SW: "Ankara {invoice_number} kutoka {org_name}"},
        {EN: "Dear {tenant_name},\n\nInvoice {invoice_number} for {unit}, {property} is attached.\n\n"
             "Amount: {amount}\nDue on: {due_date}\nBalance on your account: {balance}\n\n"
             + _PAY[EN] + "\n\n{org_name}",
         SW: "Mpendwa {tenant_name},\n\nAnkara {invoice_number} ya {unit}, {property} imeambatishwa.\n\n"
             "Kiasi: {amount}\nTarehe ya kulipa: {due_date}\nSalio la akaunti yako: {balance}\n\n"
             + _PAY[SW] + "\n\n{org_name}"},
    ),
    "rent_due_soon": (
        {EN: "Rent reminder: {amount_due} due on {due_date}",
         SW: "Ukumbusho wa kodi: {amount_due} tarehe {due_date}"},
        {EN: "Dear {tenant_name},\n\nA reminder that {amount_due} for {unit}, {property} is due on {due_date} "
             "(invoice {invoice_number}).\n\n" + _PAY[EN] + "\n\n" + _PAID[EN] + "\n\n{org_name}",
         SW: "Mpendwa {tenant_name},\n\nTunakukumbusha kuwa {amount_due} ya {unit}, {property} inalipwa tarehe "
             "{due_date} (ankara {invoice_number}).\n\n" + _PAY[SW] + "\n\n" + _PAID[SW] + "\n\n{org_name}"},
    ),
    "rent_overdue": (
        {EN: "Rent overdue: {amount_due} for {unit}", SW: "Kodi imechelewa: {amount_due} ya {unit}"},
        {EN: "Dear {tenant_name},\n\n{amount_due} for {unit}, {property} was due on {due_date} and is still "
             "unpaid (invoice {invoice_number}).\n\nTotal balance on your account: {balance}\n\n"
             + _PAY[EN] + "\n\n" + _PAID[EN] + "\n\n{org_name}",
         SW: "Mpendwa {tenant_name},\n\n{amount_due} ya {unit}, {property} ilitakiwa kulipwa tarehe {due_date} "
             "na bado haijalipwa (ankara {invoice_number}).\n\nSalio lote la akaunti yako: {balance}\n\n"
             + _PAY[SW] + "\n\n" + _PAID[SW] + "\n\n{org_name}"},
    ),
    "payment_received": (
        {EN: "Receipt {receipt_number} from {org_name}", SW: "Risiti {receipt_number} kutoka {org_name}"},
        {EN: "Dear {tenant_name},\n\nWe received {amount} for {unit}, {property} on {paid_on}. Thank you.\n\n"
             "Receipt {receipt_number} is attached.\nBalance on your account: {balance}\n\n{org_name}",
         SW: "Mpendwa {tenant_name},\n\nTumepokea {amount} ya {unit}, {property} tarehe {paid_on}. Asante.\n\n"
             "Risiti {receipt_number} imeambatishwa.\nSalio la akaunti yako: {balance}\n\n{org_name}"},
    ),
    "announcement": (
        {EN: "Message from {org_name}", SW: "Ujumbe kutoka {org_name}"},
        {EN: "Dear {tenant_name},\n\n{text}\n\n{org_name}", SW: "Mpendwa {tenant_name},\n\n{text}\n\n{org_name}"},
    ),
    "notice_received": (
        {EN: "Your notice to vacate {unit}", SW: "Notisi yako ya kuhama {unit}"},
        {EN: "Dear {tenant_name},\n\nWe have your notice to vacate {unit}, {property}.\n\nMove-out day: {move_out_on}"
             "\nBalance on your account: {balance}\n\nWe will call you to agree the move-out inspection and the "
             "handing back of keys.\n\n{org_name}",
         SW: "Mpendwa {tenant_name},\n\nTumepokea notisi yako ya kuhama {unit}, {property}.\n\nSiku ya kuhama: "
             "{move_out_on}\nSalio la akaunti yako: {balance}\n\nTutakupigia kupanga ukaguzi wa kuhama na kurudisha "
             "funguo.\n\n{org_name}"},
    ),
    "move_out_soon": (
        {EN: "You move out of {unit} on {move_out_on}", SW: "Unahama {unit} tarehe {move_out_on}"},
        {EN: "Dear {tenant_name},\n\nA reminder that you move out of {unit}, {property} on {move_out_on}.\n\n"
             "Balance on your account: {balance}\n\nPlease agree a time for the move-out inspection and handing back "
             "the keys.\n\n{org_name}",
         SW: "Mpendwa {tenant_name},\n\nTunakukumbusha kuwa unahama {unit}, {property} tarehe {move_out_on}.\n\n"
             "Salio la akaunti yako: {balance}\n\nTafadhali panga muda wa ukaguzi wa kuhama na kurudisha funguo."
             "\n\n{org_name}"},
    ),
    "maintenance_update": (
        {EN: "Repair request {number}: {status}", SW: "Ombi la matengenezo {number}: {status}"},
        {EN: "Dear {tenant_name},\n\nYour repair request {number} ({title}) for {unit}, {property}: {status}."
             "\n\n{note}\n\n{org_name}",
         SW: "Mpendwa {tenant_name},\n\nOmbi lako la matengenezo {number} ({title}) la {unit}, {property}: "
             "{status}.\n\n{note}\n\n{org_name}"},
    ),
}


def _with_email(ntype: "NotificationType") -> "NotificationType":
    if ntype.codename not in _EMAIL:
        return ntype
    subjects, bodies = _EMAIL[ntype.codename]
    return replace(ntype, channels=(*ntype.channels, EMAIL), subjects=subjects,
                   bodies={**ntype.bodies, **{(EMAIL, lang): text for lang, text in bodies.items()}})


def email_copy(ntype: "NotificationType") -> bool:
    """Whether the type can send tenants an email copy (D-076)."""
    return (EMAIL, EN) in ntype.bodies


TYPES = tuple(_with_email(_with_whatsapp(t)) for t in _TYPES)
BY_CODENAME: dict[str, NotificationType] = {t.codename: t for t in TYPES}
CHOICES = [(t.codename, t.label) for t in TYPES]
CHANNEL_CHOICES = [(SMS, _("SMS")), (WHATSAPP, _("WhatsApp")), (EMAIL, _("Email")), (IN_APP, _("In-app"))]
LANGUAGE_CHOICES = [(EN, _("English")), (SW, _("Swahili"))]


_CACHE_KEY = "notifications.type_settings"
_CACHE_SECONDS = 60


def _platform_settings() -> dict:
    """The owner's changes to the code defaults, set in Django admin: {codename: setting}. Cached briefly."""
    from django.core.cache import cache

    found = cache.get(_CACHE_KEY)
    if found is None:
        from django.db import DatabaseError

        from .models import NotificationTypeSetting

        try:
            found = {row.type: row for row in NotificationTypeSetting.objects.all()}
        except (DatabaseError, RuntimeError):
            return {}  # no database yet (before migrating) or none allowed: the code defaults, not cached
        cache.set(_CACHE_KEY, found, _CACHE_SECONDS)
    return found


def clear_platform_settings() -> None:
    from django.core.cache import cache

    cache.delete(_CACHE_KEY)


def _apply(ntype: NotificationType, setting) -> NotificationType:
    """The code type with the platform owner's changes on top. Channels the type has no wording for are ignored."""
    if setting is None:
        return ntype
    channels = tuple(c for c in setting.channels if c in ntype.channels) or ntype.channels
    return replace(
        ntype, enabled=setting.enabled or setting.mandatory, channels=channels, mandatory=setting.mandatory,
        urgent=setting.urgent, offsets=tuple(setting.offsets) if setting.offsets and ntype.offsets else ntype.offsets)


def get(codename: str) -> NotificationType:
    try:
        base = BY_CODENAME[codename]
    except KeyError:
        raise ValueError(f"Unknown notification type {codename!r}") from None
    return _apply(base, _platform_settings().get(codename))


def types() -> tuple[NotificationType, ...]:
    """Every type as it works now: the code defaults with the platform owner's changes."""
    settings = _platform_settings()
    return tuple(_apply(t, settings.get(t.codename)) for t in TYPES)
