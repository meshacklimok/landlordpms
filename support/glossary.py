"""The glossary: what the app's words mean. One list serves the Help page (templates/support/help/glossary.html)
and the assistants' define_term tool, so the page and the assistants never disagree.

`tenant` marks terms a tenant may be told about; the tenant assistant never sees the rest (staff-only workings).
`topic` is the help topic that explains the term in full. `words` are other names people use for it.
"""

import re
from dataclasses import dataclass

from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class Term:
    key: str
    name: str
    meaning: str
    topic: str = ""
    tenant: bool = False
    words: str = ""


GROUPS: list[tuple[str, list[Term]]] = [
    (_("Money and billing"), [
        Term("invoice", _("Invoice"), _("The bill for one period of a lease: rent plus any water, electricity or other "
             "charges. A lease is billed monthly, quarterly or yearly and the tenant gets the invoice by SMS."),
             "invoices-payments", True, "bill statement"),
        Term("overdue", _("Due date, grace days and overdue"), _("The due date is the lease's due day. Grace days are "
             "extra days allowed after it. An invoice still unpaid after the grace days is overdue."),
             "invoices-payments", True, "late due"),
        Term("arrears", _("Arrears"), _("Rent and charges that are overdue and unpaid. The call list shows who owes "
             "and who to call today."), "invoices-payments", False, "owing unpaid call list"),
        Term("balance", _("Balance owed and in credit"), _("A lease's balance is what the tenant owes across all "
             "unpaid invoices. A negative balance is shown as <em>in credit</em>: the tenant has paid more than is "
             "owed."), "invoices-payments", True, "outstanding balance"),
        Term("credit", _("Credit"), _("Money paid beyond what was owed, or not allocated to an invoice. It stays on "
             "the account as credit and is not lost. It is left out of the receivables report."),
             "invoices-payments", True, "overpayment prepayment advance"),
        Term("allocation", _("Allocation"), _("Which invoices a payment pays. By default a payment pays the oldest "
             "invoice first. When recording a payment you can instead type how much goes to each invoice; whatever "
             "is not allocated is kept as credit."), "invoices-payments", False, "allocate apply oldest first"),
        Term("receipt", _("Receipt"), _("A numbered proof issued for every confirmed payment. The tenant gets a "
             "link to it."), "invoices-payments", True, "proof"),
        Term("void", _("Void and reverse"), _("Nothing is deleted. A wrong invoice is <em>voided</em>; a wrong "
             "payment is <em>reversed</em> with a reason. Both stay in the record."), "invoices-payments", False,
             "cancel delete undo correction"),
        Term("late-fee", _("Late fee"), _("An optional charge on an overdue invoice, as a fixed amount or a "
             "percentage of what is unpaid with a maximum. Each fee waits for staff to charge or skip it; charging "
             "sends the tenant an invoice for the fee."), "invoices-payments", True, "penalty"),
        Term("deposit", _("Deposit"), _("Money a tenant pays at the start of a lease and which is accounted for when "
             "it ends. It is not rent."), "tenants-leases", True, "security refund"),
        Term("charge-type", _("Charge type"), _("A named kind of charge that can appear on an invoice, such as Water "
             "or Metered electricity, so that charges are listed and reported separately from rent."),
             "invoices-payments", False, "line item"),
        Term("receivables", _("Receivables"), _("Everything tenants owe on a given day, split by property and by how "
             "late it is. Credit balances are not included."), "", False, "ageing aging debtors"),
        Term("collection-rate", _("Collection rate"), _("The share of the rent that was due in a period that was "
             "actually collected."), "", False, "collected expected"),
        Term("cash-basis", _("Cash basis"), _("A figure counted when money is received, not when it was invoiced. "
             "Reports say when they use it, because it can differ from the invoiced figure."), "", False,
             "accrual received"),
        Term("move-out-statement", _("Move-out statement"), _("The final account when a tenancy ends: the deposit, "
             "less any balance owed or plus any credit, so the amount to refund or collect is clear."),
             "tenants-leases", False, "final account refund settlement"),
        Term("accountant-export", _("Accountant export"), _("A ZIP for chosen months with a double-entry journal "
             "that imports into QuickBooks, Zoho Books or Wave, a list of accounts with their totals, and the "
             "invoices, payments and expenses."), "invoices-payments", False, "journal double-entry ledger"),
    ]),
    (_("M-Pesa and banks"), [
        Term("paybill", _("Paybill"), _("An M-Pesa business number where the payer types an account number. Payments "
             "with the right account number are matched to the tenant automatically. A Paybill is recommended."),
             "mpesa", True, "shortcode business number"),
        Term("till", _("Till (Buy Goods)"), _("An M-Pesa number with no account number, so payments are not matched "
             "on their own. Each waits in the inbox for you to match."), "mpesa", False, "buy goods"),
        Term("reference", _("Payment reference (account number)"), _("The code unique to a unit that the tenant types "
             "as the account number, so the payment is matched to the right lease."), "mpesa", True,
             "account number"),
        Term("stk", _("Payment request (STK push)"), _("A prompt sent to the tenant's phone asking them to enter "
             "their M-Pesa PIN to pay. It needs a Paybill."), "mpesa", True, "stk push prompt"),
        Term("daraja", _("Daraja"), _("Safaricom's developer service. You create an app on it and put its shortcode, "
             "consumer key, secret and (for a Paybill) passkey in M-Pesa settings so we can see your payments. We "
             "never hold your money."), "mpesa", False, "safaricom consumer key secret passkey"),
        Term("payment-links", _("Register the payment links"), _("The step that tells Safaricom where to send a "
             "notice each time someone pays your Paybill or Till (the C2B URLs). Until it is done, payments do not "
             "arrive in the app."), "mpesa", False, "c2b urls callback register"),
        Term("inbox", _("M-Pesa inbox"), _("Payments that could not be matched on their own. Each comes with a "
             "suggestion for you to match or ignore."), "mpesa", False, "unmatched unallocated"),
        Term("transaction-code", _("Transaction code"), _("The code M-Pesa gives each payment, as in the confirmation "
             "SMS. Use it to find a payment."), "mpesa", True, "mpesa code confirmation"),
        Term("bank-import", _("Bank statement import"), _("Uploading a bank statement so its money-in lines can be "
             "matched to tenants. Lines already imported from an earlier statement are skipped, so overlapping "
             "statements are fine. A matched line is confirmed at once, pays the oldest invoices first and gets a "
             "receipt."), "mpesa", False, "bank reconcile reconciliation csv"),
        Term("ussd", _("USSD"), _("A short code a tenant dials on any phone, with no internet, to check a balance, pay "
             "or report a repair. A tenant may protect it with a PIN."), "", True, "dial code pin"),
        Term("card", _("Card payment"), _("Paying our subscription with a Visa or Mastercard through Paystack. It "
             "is used for the subscription only; tenants' rent goes to your own Paybill or Till."),
             "subscription", False, "paystack visa mastercard"),
    ]),
    (_("Leases and tenants"), [
        Term("lease", _("Lease"), _("A tenant's tenancy of one unit, with its rent, due day, charges and dates. "
             "Invoices are issued per lease."), "tenants-leases", True, "tenancy agreement"),
        Term("unit", _("Unit and property"), _("A property is a building or estate; a unit is one let-able place in "
             "it, such as a flat or a room."), "properties-units", True, "house flat room building"),
        Term("vacant", _("Vacant and let"), _("A unit with no active lease is vacant. A unit with one is let."),
             "properties-units", False, "empty occupied"),
        Term("notice-to-vacate", _("Notice to vacate"), _("Written notice that a tenancy is ending, recorded with "
             "its date and history and shown to the tenant in their portal."), "tenants-leases", True,
             "leaving move out"),
        Term("demand-letter", _("Demand letter"), _("A formal letter asking for overdue rent, kept with a record of "
             "when and how it was served."), "tenants-leases", False, "arrears notice"),
        Term("service-record", _("Service record"), _("The record of when, how and by whom a letter or notice was "
             "delivered to the tenant. It is what you rely on if the notice is disputed."), "tenants-leases", False,
             "served delivery proof"),
        Term("renewal-offer", _("Renewal offer"), _("An offer of a new term sent through the tenant portal. When the "
             "tenant accepts, staff start a draft lease."), "tenants-leases", True, "extend renew"),
        Term("esign", _("E-signature"), _("Signing a document online instead of on paper. Each signed document "
             "records who signed and when."), "documents", True, "sign signature"),
        Term("fingerprint", _("Fingerprint (SHA-256)"), _("A long code worked out from a document's contents. If one "
             "letter of the document changes, the code changes, so comparing codes proves a copy was not altered."),
             "documents", False, "sha256 sha-256 hash checksum"),
        Term("evidence-pack", _("Evidence pack"), _("A download for a signed document with its files, signers, audit "
             "trail and a certificate to sign, for use if the document is ever needed in court."), "documents",
             False, "audit trail court"),
        Term("s106b", _("Section 106B certificate"), _("A certificate under section 106B of the Evidence Act that "
             "explains how a computer record was produced, so it can be used as evidence in court. It is filled in "
             "and signed by a person; the app does not give legal advice."), "", False, "evidence act court"),
        Term("handover-pack", _("Handover pack"), _("A ZIP for your advocate with a brief, the statement of account, "
             "payments, the letters as served, move-out papers and a section 106B certificate to complete. It is "
             "drawn from the records when you download it."), "tenants-leases", False, "advocate lawyer"),
        Term("prospect", _("Prospect"), _("Someone asking about a unit, with their viewings and any unit held for "
             "them. When they take the unit they become a tenant."), "prospects", False, "enquiry applicant"),
    ]),
    (_("Meters and water"), [
        Term("reading", _("Meter reading"), _("The number on a meter at a point in time. The difference from the last "
             "reading is the use that is billed. The first reading is a starting point and bills nothing."),
             "water", False, "usage consumption"),
        Term("shared-meter", _("Shared meter"), _("One meter serving several units. Its bill is divided equally, by "
             "weight or by percentage (percentages must add up to 100)."), "water", True, "split weight"),
        Term("water-plan", _("Water plan"), _("How a property charges for water: by <em>meter readings</em>, a "
             "<em>flat amount</em> a month, or <em>included in the rent</em>."), "water", True,
             "flat metered included"),
        Term("flagged", _("Flagged reading"), _("A reading that looks unusual: lower than last time, no use, much "
             "higher or a long gap. It needs a note and approval before it is billed."), "water", False,
             "approve unusual"),
        Term("minimum-charge", _("Minimum charge"), _("The lowest amount a meter bills in a month even when little or "
             "nothing was used."), "water", True, "standing charge"),
        Term("prepaid-meter", _("Prepaid token meter"), _("A meter the tenant tops up directly with tokens. It is "
             "tracked but not billed."), "water", True, "token"),
        Term("qr-scan", _("QR label and photo scan"), _("A QR label on a meter opens that meter's reading form. A "
             "photo of a notebook is read and filled in for you to check before anything is saved."), "water",
             False, "ocr scan notebook"),
    ]),
    (_("Repairs"), [
        Term("repair-targets", _("Targets: late to start and late to fix"), _("Each repair has a time by which work "
             "should start and a time by which it should be fixed, from its priority. Past either, it shows as late."),
             "maintenance", True, "sla response overdue"),
        Term("priority", _("Priority"), _("How urgent a repair is. It sets the start and fix targets."),
             "maintenance", True, "emergency urgent"),
        Term("supplier", _("Supplier"), _("A plumber, electrician or other contractor kept in the app so a repair can "
             "be assigned to them."), "maintenance", False, "contractor"),
    ]),
    (_("Team, security and portals"), [
        Term("role", _("Role and permission"), _("A role is a set of permissions that decide what a staff member can "
             "see and do, for example confirm payments or approve readings."), "staff-roles", False,
             "access rights"),
        Term("two-step", _("Two-step login"), _("A second code from an authenticator app asked for after the password. "
             "An owner can require it of every member of staff."), "staff-roles", False, "mfa 2fa authenticator"),
        Term("audit", _("Audit trail"), _("The record of who did what and when. Entries cannot be edited or removed."),
             "", False, "audit log history"),
        Term("api-key", _("API key"), _("A secret that lets another program use your NyumbaPMS data through the API. "
             "Treat it like a password."), "staff-roles", False, "api token"),
        Term("webhook", _("Webhook"), _("A web address of yours that we call each time something happens, such as a "
             "payment, so your other systems stay in step."), "staff-roles", False, "event callback"),
        Term("signing-secret", _("Signing secret"), _("The key, shown once, used to sign each webhook event "
             "(HMAC-SHA256) so your system can check the event really came from us."), "staff-roles", False,
             "hmac signature"),
        Term("tenant-portal", _("Tenant portal"), _("Where tenants see invoices, pay, report repairs, message you and "
             "respond to offers."), "tenants-leases", True, "my"),
        Term("owner-portal", _("Owner portal"), _("A view for a property's owner when an agency manages it. Tenant "
             "names are hidden."), "staff-roles", False, "landlord agency"),
        Term("sms-credit", _("SMS credit"), _("The balance used to send SMS once the plan's included messages are "
             "used up. With none, messages go by email where possible."), "messages", False, "sms balance"),
        Term("sender-name", _("Sender name"), _("The name SMS show as the sender. It is the platform's name unless "
             "your own is approved."), "messages", False, "sender id"),
        Term("quiet-hours", _("Quiet hours and STOP"), _("Quiet hours are times when reminders are not sent. A tenant "
             "who replies STOP stops optional messages; required ones are still sent."), "messages", True, "opt out"),
        Term("read-only", _("Read-only"), _("An account whose subscription has lapsed. You can still see and export "
             "everything but cannot add anything new until you pay."), "subscription", False, "lapsed frozen"),
        Term("plan-credit", _("Plan change credit"), _("Changing plan mid-period gives you credit for the days you "
             "have not used."), "subscription", False, "prorate prorata proration"),
    ]),
    (_("Reports"), [
        Term("occupancy", _("Occupancy"), _("The share of your units that are let."), "", False, "occupied"),
        Term("paid-in-time", _("Paid in time"), _("Whether a month's rent was paid by its due date, shown for each "
             "unit across 12 months."), "", False, "on time punctual"),
        Term("empty-days", _("Empty days"), _("The days a unit had no tenant, which is rent you did not earn."), "",
             False, "vacancy loss"),
        Term("rent-vs-asking", _("Rent vs asking"), _("What a unit's tenant pays compared with the rent you list for "
             "the unit."), "", False, "market"),
    ]),
]

TERMS = [t for _g, terms in GROUPS for t in terms]
_TAG = re.compile(r"<[^>]+>")


def plain(text) -> str:
    return _TAG.sub("", str(text))


def lookup(text: str, tenant_only: bool = False, limit: int = 4) -> list[Term]:
    """Terms matching what was asked about, best first: by name or alias, then by words in the meaning."""
    wanted = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
    if not wanted:
        return []
    pool = [t for t in TERMS if t.tenant or not tenant_only]
    scored = []
    for t in pool:
        names = " ".join(re.findall(r"[a-z0-9]+", f"{plain(t.name)} {t.words}".lower()))
        if wanted in names:
            score = 3
        elif any(w in names.split() for w in wanted.split() if len(w) > 2):
            score = 2
        elif wanted in plain(t.meaning).lower():
            score = 1
        else:
            continue
        scored.append((-score, plain(t.name), t))
    return [t for _s, _n, t in sorted(scored, key=lambda x: x[:2])[:limit]]
