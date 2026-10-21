"""About, How it works, the roadmap and articles for visitors (D-135). Short on purpose. Like content.py, no prices
are written here, and nothing is promised that is not already built unless it sits under "Not yet"."""

from django.utils.translation import gettext_lazy as _

ABOUT = {
    "intro": _("NyumbaPMS is rent software made for how Kenyan landlords actually collect: M-Pesa, SMS, a caretaker "
               "with a phone, and tenants who may not have a smartphone."),
    "why": [
        _("Rent is usually tracked in a notebook, a spreadsheet and an M-Pesa statement that never agree."),
        _("Landlords lose time matching payments and chasing arrears, and tenants argue over what they paid."),
        _("We built one place where a payment is recorded once, matched to the right unit, and receipted."),
    ],
    # (icon, title, text)
    "principles": [
        ("wallet2", _("Your money goes straight to you"),
         _("Tenants pay into your own Paybill or Till. We never hold rent.")),
        ("journal-check", _("Nothing is quietly deleted"),
         _("Mistakes are reversed with a reason, so every figure can be checked later.")),
        ("person-check", _("A person decides"),
         _("Late fees, notices and the assistant's actions wait for your confirmation.")),
        ("phone", _("Works on a basic phone"),
         _("Tenants can use a short code. Staff can take meter readings with no signal.")),
        ("box-arrow-up-right", _("Your data is yours"),
         _("Export everything at any time, and ask for deletion when you leave.")),
    ],
    # (icon, who)
    "for": [("house-door", _("Landlords with a few units")), ("person-badge", _("Caretakers and property managers")),
            ("briefcase", _("Agencies managing for owners")), ("buildings", _("Gated estates")),
            ("droplet", _("Water and garbage services"))],
}


HOW_STEPS = [
    (_("Set up"), _("Add your properties, units and tenants, or load a spreadsheet. Start a lease with the rent, "
                    "due day and deposit.")),
    (_("Bill"), _("Invoices are made each month. Water and electricity from approved meter readings, and recurring "
                  "charges such as garbage, go on the same invoice.")),
    (_("Collect"), _("Tenants pay by M-Pesa to your Paybill using their unit reference. Cash and bank payments are "
                     "recorded by hand or from a bank statement.")),
    (_("Match and receipt"), _("Each payment goes against the oldest unpaid invoice and gets a numbered receipt. "
                               "Payments that do not match wait in an inbox for one tap.")),
    (_("Follow up"), _("Reminders and overdue notices go out by SMS. The arrears page lists who to call today.")),
    (_("See and prove"), _("Reports show income, arrears and vacancies by property and unit. Letters, notices and "
                           "a handover pack for your advocate come from the same records.")),
]


PLANNING = [
    (_("1. Start with the money"),
     _("Rent, payments, receipts and balances first, because everything else depends on them being right.")),
    (_("2. Add what landlords asked for"),
     _("Meters, repairs, notices, owner and tenant portals, then reports. Each was built after a real need.")),
    (_("3. Keep it checkable"),
     _("An audit log, reversals instead of deletes, and separate data per landlord, from the start.")),
    (_("4. Test with a few landlords"),
     _("A pilot on one server, with real Paybills, before we grow. We fix what they find.")),
]


# (heading, items). "Done" is built and in use; "In progress" is being built or in pilot; "Not yet" is planned only.
ROADMAP = [
    (_("Built"), "success", [
        _("Rent billing, M-Pesa matching, receipts, reminders and arrears"),
        _("Tenant portal, phone menu (USSD) and WhatsApp questions"),
        _("Meters, repairs, late fees, notices to vacate and demand letters"),
        _("Reports, owner portal, agency fees, staff roles and two-step login"),
        _("Install on a phone home screen, offline meter rounds, spreadsheet import and export"),
    ]),
    (_("In progress"), "warning", [
        _("Pilot with the first landlords and agencies"),
        _("Account deletion done by us on request; no self-serve button yet"),
        _("Articles and help pages, still being added"),
    ]),
    (_("Not yet"), "secondary", [
        _("A store app for Android and iPhone (today it installs from the browser)"),
        _("Direct bank connections, so bank deposits arrive without uploading a statement"),
        _("Tenant screening"),
        _("More languages than English"),
    ]),
]


# Extra FAQ for people who already use it or read the details. Added after the basic groups on /faq/.
ADVANCED_FAQ = [
    (_("Advanced: payments and matching"), [
        (_("Can tenants pay automatically every month?"),
         _("M-Pesa has no direct debit, so we do not promise it. A tenant can set a standing order in the "
           "MySafaricom app, and our reminder goes out a few days before the due date. Either way the payment "
           "arrives on your Paybill and is matched as usual.")),
        (_("How do I check my records against my Paybill statement?"),
         _("Every M-Pesa payment is stored with its code, so you can put the Safaricom statement beside your "
           "payments list and tick them off. Anything on the statement that is not matched to a unit is waiting "
           "in the M-Pesa inbox.")),
        (_("What does M-Pesa charge for a rent payment?"),
         _("Safaricom's tariff for a Paybill depends on the amount and on who pays the fee. We do not set it and "
           "do not add to it. Ask Safaricom or your Paybill agreement for the current table.")),
        (_("How is a payment matched to a unit?"),
         _("By the account number the tenant typed, compared with each unit's reference. A match is recorded and "
           "receipted; any other waits in the inbox for you to confirm or ignore.")),
        (_("What if Safaricom sends the same payment twice?"),
         _("Each M-Pesa code is recorded once. A repeat of the same code is ignored, so a payment cannot be counted "
           "twice.")),
        (_("What if a payment is wrong?"),
         _("Reverse it with a reason. The original stays in the history and balances are corrected.")),
        (_("What if a tenant overpays?"),
         _("The payment goes against the oldest unpaid invoices first. Ask us how an amount beyond what is owed "
           "is shown on your statement.")),
        (_("Can one tenant pay for two units?"),
         _("Yes, if the reference of each is used. Otherwise match the payment by hand in the inbox.")),
    ]),
    (_("Advanced: billing"), [
        (_("Can I manage my property from abroad?"),
         _("Yes. Everything works in a browser. Your caretaker or agent takes the readings and records cash, "
           "you see payments, arrears and vacancies as they happen, and reminders go out without you. The "
           "money goes straight to your Paybill, not through us.")),
        (_("When are invoices created?"),
         _("A few days before each month starts. You can also press Generate to see one now.")),
        (_("Can I change rent mid-lease?"),
         _("Yes. Add a rent change from a date; earlier invoices keep the old rent.")),
        (_("How is a part month billed?"),
         _("By days, unless you set your workspace to bill partial months in full.")),
        (_("What happens to the deposit?"),
         _("It is recorded on the lease. At move-out you list deductions and refund the balance, all on record.")),
    ]),
    (_("Advanced: staff and access"), [
        (_("Can I make my own role?"),
         _("Yes. Copy a built-in role and switch capabilities on or off. You can also limit it to chosen properties.")),
        (_("Can two-step login be required?"),
         _("Yes. The owner can require it for everyone in the workspace.")),
        (_("Is there an audit log?"),
         _("Yes. Important actions record who, what and when. It cannot be edited.")),
    ]),
    (_("Advanced: integrations"), [
        (_("Is there an API?"),
         _("Yes, a read API and some writes, with keys you create and can revoke. Webhooks tell your own system "
           "when a payment arrives.")),
        (_("Does it sync with QuickBooks or ERPNext?"),
         _("Not directly. You can export a journal and the records as CSV for your accountant.")),
    ]),
    (_("Advanced: leaving and deleting"), [
        (_("How do I delete my account?"),
         _("Ask from the Delete account page, signed in as the owner. We confirm with you, give you 30 days to "
           "export, and delete within 90 days. Records the law requires us to keep are kept.")),
        (_("What is not deleted?"),
         _("Our own invoices to you (kept at least five years for tax), and signing and audit records needed as "
           "evidence. Everything else is removed or anonymised.")),
        (_("What about my tenants' data?"),
         _("It is yours as the landlord. Export it before you close. After deletion we cannot restore it.")),
        (_("Can a tenant delete their data?"),
         _("A tenant asks their landlord first. If the landlord cannot be reached, they can write to us.")),
    ]),
]
