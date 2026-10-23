"""The public FAQ and tutorials (D-133). Written from the signed-in help pages (support/help/), which stay the
reference; these are the short version for someone who has not signed up yet. Prices are never written here:
the pricing page reads them from the plans."""

from django.utils.translation import gettext_lazy as _

FEATURES = [
    ("phone", _("Rent on your own Paybill"),
     _("Tenants pay into your Paybill or Till. Each payment is recorded against the right unit and a numbered "
       "receipt goes to the tenant.")),
    ("chat-left-text", _("Reminders that run themselves"),
     _("Invoices, reminders and overdue notices go out by SMS, with quiet hours and a call list for who to phone today.")),
    ("bar-chart-line", _("Figures you can trust"),
     _("Income against billed, arrears, vacancies and expenses, by property and unit, on one page.")),
    ("droplet", _("Water and electricity by meter"),
     _("Take readings on a phone, even offline, approve them, and bill what was used.")),
    ("people", _("Your team and your tenants"),
     _("Invite a caretaker or accountant with only the access they need. Tenants see their own balance and receipts, "
       "also by a short code on a basic phone.")),
    ("shield-check", _("Your records stay yours"),
     _("Nothing is deleted, only reversed with a reason. Export everything whenever you like.")),
]


# The home page (D-142). Written after comparing ten property management sites; only what is built is claimed here.
# (icon, number, label): facts, not invented customer numbers.
LANDING_FACTS = [
    ("wallet2", _("100%"), _("of rent goes straight to your own Paybill or Till")),
    ("calendar-check", _("30 days"), _("free trial with everything on, no card")),
    ("grid-3x3-gap", _("Every feature"), _("on every plan, priced only by units")),
    ("cash-coin", _("KSh"), _("prices in shillings, M-Pesa first")),
]

# (the old way, what happens here): the problem the visitor has today, then the answer, side by side.
LANDING_SWAPS = [
    (_("Scrolling M-Pesa messages to see who paid"), _("Each payment lands on its unit, with a receipt sent")),
    (_("Calling every tenant at month end"), _("Reminders go out by SMS; you call only who is late")),
    (_("A notebook of meter readings"), _("Readings on a phone, billed with the rent")),
    (_("Guessing what the building made"), _("Income, arrears and vacancies on one page")),
]

# Things the product works with, shown as plain words under the hero (never other companies' logos).
LANDING_WORKS_WITH = [
    ("phone", _("M-Pesa Paybill and Till")), ("chat-left-text", _("SMS")), ("whatsapp", _("WhatsApp")),
    ("bank", _("Bank statements")), ("file-earmark-spreadsheet", _("Excel and CSV")), ("telephone", _("Short code")),
]

# (icon, who, what they get, slug of the solutions page that answers them)
LANDING_AUDIENCES = [
    ("house-door", _("Landlords"), _("From a few units to a plot of flats: bills, receipts and who owes, on your phone."),
     "mpesa-rent-collection"),
    ("briefcase", _("Agents and managers"), _("Many owners, one login each. Owner statements and your own fees on one page."),
     "software-for-agents-and-property-managers"),
    ("buildings", _("Gated estates"), _("Service charge, water and garbage on one invoice per household."),
     "estate-service-charge-and-water-billing"),
    ("droplet", _("Water and garbage services"), _("Meter-based bills and monthly charges for hundreds of households."),
     "water-vendor-billing-software"),
    ("person-badge", _("Caretakers"), _("Meter readings and repair updates with only the access you give."),
     "water-and-electricity-billing"),
    ("globe-europe-africa", _("Landlords abroad"), _("See collections, arrears and photos without calling anyone."),
     "manage-rental-property-in-kenya-from-abroad"),
]

# Deep sections: (anchor, eyebrow, heading, intro, [(icon, point)]).
LANDING_BLOCKS = [
    ("collect", _("Collect"), _("Rent that matches itself"),
     _("Tenants pay with the unit reference. Each payment lands on the right unit and the oldest unpaid invoice, "
       "and a numbered receipt goes out."),
     [("phone", _("Your own Paybill or Till; we never hold the money")),
      ("receipt", _("Numbered receipts by SMS, email or WhatsApp")),
      ("inbox", _("Payments with a wrong reference wait in an inbox for one tap")),
      ("bank", _("Cash and bank payments recorded by hand or from a statement")),
      ("telephone", _("Tenants can check and pay from a basic phone with a short code"))]),
    ("follow-up", _("Follow up"), _("Chase less, keep the records"),
     _("Reminders go out on their own. When a tenant falls behind, the steps are ready and a person decides each one."),
     [("chat-left-text", _("SMS reminders with quiet hours, and WhatsApp for questions")),
      ("telephone", _("An arrears list of who to call today")),
      ("percent", _("Late fees by your rule, charged only when you confirm")),
      ("file-earmark-text", _("Notices to vacate and demand letters, with a record of service")),
      ("folder-check", _("A handover pack for your advocate if it goes further"))]),
    ("manage", _("Manage"), _("The whole building in one place"),
     _("Leases, meters, repairs and paperwork sit next to the money, so nothing lives in a notebook."),
     [("droplet", _("Water and electricity from meter readings, even without signal")),
      ("tools", _("Repairs with photos, a supplier or caretaker, and the cost as an expense")),
      ("clipboard-check", _("Move-in and move-out condition reports with photos")),
      ("pen", _("Tenancy agreements signed on a phone, with a signing record")),
      ("people", _("Viewings and holds for empty units, then one tap to make them tenants"))]),
    ("see", _("See and prove"), _("Figures you can check"),
     _("Every number comes from a payment or an invoice, and every change leaves a trail."),
     [("bar-chart-line", _("Income, arrears and vacancies by property and by unit")),
      ("person-lines-fill", _("Owner logins that show statements without tenant names")),
      ("journal-check", _("Reversals with a reason instead of deletes, and an audit log")),
      ("download", _("Export everything to spreadsheets whenever you like")),
      ("shield-lock", _("Two-step login and access by role"))]),
]

# (icon, label): the rest of the list, shown as a compact grid.
LANDING_MORE = [
    ("stars", _("Ask the assistant in plain words")), ("plug", _("Open API and webhooks")),
    ("upload", _("Import from a spreadsheet")), ("person-gear", _("Tenant portal with balance and receipts")),
    ("piggy-bank", _("Deposits and refunds")), ("receipt-cutoff", _("Expenses and suppliers")),
    ("arrow-repeat", _("Recurring charges such as garbage")), ("envelope-paper", _("Renewal offers")),
    ("folder2-open", _("Documents per unit and lease")), ("phone-vibrate", _("Install on a phone home screen")),
    ("chat-dots", _("Message a tenant in your own words")), ("sliders", _("Your own SMS sender name")),
]


FAQ = [
    (_("The basics"), [
        (_("What is NyumbaPMS?"),
         _("Software for landlords, agents, estates and water or garbage services in Kenya. It bills tenants each "
           "month, records M-Pesa payments against the right unit, sends reminders by SMS, and shows who owes what, "
           "on a phone or a computer.")),
        (_("Who is it for?"),
         _("A landlord with a few units, a caretaker, a property manager or an agency looking after other people's "
           "buildings, a gated estate, or a water or garbage service that bills many households. Agencies can give "
           "each owner a login to see their own statements.")),
        (_("Is there a free trial?"),
         _("Yes. Every new workspace starts with a 30-day trial with everything switched on. You do not need to "
           "give a card to start.")),
        (_("What does it cost?"),
         _("Plans are by the number of units you manage and every plan has every feature. Prices are on the "
           "pricing page, before VAT.")),
        (_("Do I need to install anything?"),
         _("No. It opens in the browser, and you can add it to your phone's home screen like an app. Tenants do not "
           "need to install anything either.")),
    ]),
    (_("Rent and M-Pesa"), [
        (_("Do you hold our rent money?"),
         _("Never. Tenants pay into your own Paybill or Till, and the money goes straight to you. We only read the "
           "payment notice from Safaricom to record it.")),
        (_("Paybill or Till?"),
         _("Use a Paybill if you can. A Paybill has an account number, so each payment is matched to the right "
           "unit by itself. A Till has none, so each payment waits in your inbox for you to match with one tap. "
           "Payment requests to the tenant's phone also need a Paybill.")),
        (_("What if I do not have a Paybill?"),
         _("You can still record cash, bank and cheque payments by hand, and add your own payment instructions to "
           "the tenant's message.")),
        (_("What happens when a tenant pays the wrong account number?"),
         _("The payment is not lost. It waits in the M-Pesa inbox with a suggested match, and you confirm or "
           "ignore it. Nothing is guessed.")),
        (_("Can tenants pay part of the rent?"),
         _("Yes. A payment goes against the oldest unpaid invoice first, and the balance carries until it is "
           "cleared. Every payment gets a numbered receipt the tenant can open.")),
        (_("Can it charge late fees?"),
         _("Only if you switch them on. You choose a fixed amount or a percentage with a maximum. Each late "
           "invoice gets a proposed fee that waits for you to charge or skip it; nothing is charged behind your "
           "back.")),
        (_("Can I record bank deposits too?"),
         _("Yes. Upload your bank statement and match each line to a lease, or record a bank or cash payment by "
           "hand.")),
    ]),
    (_("Tenants"), [
        (_("Do tenants need a smartphone?"),
         _("No. Tenants with a basic phone can dial a short code to see their balance, how to pay, and their last "
           "payment, and can ask for an M-Pesa prompt. Tenants with a smartphone also get a private portal and can "
           "ask on WhatsApp.")),
        (_("What do tenants see?"),
         _("Only their own balance, statement, receipts, lease terms, repair requests and messages with your office. "
           "They never see other tenants.")),
        (_("Can tenants report repairs?"),
         _("Yes, from the portal or the phone menu. You assign it to staff or a supplier and the tenant sees the "
           "progress.")),
    ]),
    (_("Setting up"), [
        (_("How long does it take to set up?"),
         _("A landlord with a handful of units is usually billing within an afternoon: add the property and units, "
           "add tenants, start leases, and the first invoices follow. Bigger portfolios can load a spreadsheet.")),
        (_("Can I bring my records from Excel?"),
         _("Yes. Download the template, fill it in, and upload it. Every row is checked and shown to you before "
           "anything is saved. You can also send us your spreadsheet in any shape and we will import it.")),
        (_("Can my caretaker or accountant use it?"),
         _("Yes. Invite them by SMS and choose what each person can do, and which properties they see. People who "
           "can only look do not count against your plan's team members.")),
    ]),
    (_("Features"), [
        (_("Can it bill water and electricity by meter?"),
         _("Yes. Staff take readings on a phone, even without a signal in the meter room, someone approves them, "
           "and the charge goes on next month's invoice.")),
        (_("Can it produce agreements and notices?"),
         _("It prints tenancy agreements, notices to vacate and demand letters from what is on the lease, each "
           "numbered and checkable. Have the wording checked by your own advocate before relying on it. Short "
           "leases can be signed on the phone, with a signing record.")),
        (_("Can I see how my properties are doing?"),
         _("Yes. Reports show income against what was billed, arrears, vacancies, repairs and expenses, "
           "unit by unit and property by property.")),
        (_("Can it connect to my accounting software?"),
         _("It exports a journal and the underlying records for QuickBooks, Zoho Books or Wave, and has an API for "
           "other programs.")),
        (_("Can I ask it questions?"),
         _("Yes. The built-in assistant answers questions about rent, arrears and vacancies from your own figures. "
           "The owner can switch it off.")),
    ]),
    (_("Your data and safety"), [
        (_("Who can see our data?"),
         _("Only the people you invite, and only what their role allows. Each landlord's records are kept apart "
           "from every other landlord's. Important actions go into a log nobody can edit.")),
        (_("Can I take my data with me?"),
         _("Yes, at any time, including after a subscription lapses: tenants, invoices, payments and statements "
           "export as spreadsheets.")),
        (_("Is it covered by the Data Protection Act?"),
         _("You are the data controller for your tenants' records and we process them for you. Our Privacy Policy "
           "and Security pages say how, and who to write to with questions.")),
        (_("What if I stop paying?"),
         _("You get time and reminders first. If the subscription lapses your workspace becomes read-only, so you "
           "can still look at and export your records.")),
    ]),
    (_("Help"), [
        (_("How do I get help?"),
         _("Signed-in users have a help centre and a contact form that creates a numbered request. You can also "
           "book a demo and we will walk you through it.")),
    ]),
]


from .pages import ADVANCED_FAQ  # noqa: E402  (written beside the other public pages)

FAQ = FAQ + ADVANCED_FAQ


def faq_pairs():
    return [(str(q), str(a)) for _heading, items in FAQ for q, a in items]


TUTORIALS = [
    {"slug": "first-invoice", "title": _("From sign-up to your first invoice"), "minutes": 20,
     "summary": _("Set up a property, a tenant and a lease, and see the first rent invoice."),
     "help": "getting-started",
     "steps": [
         (_("Create your account"),
          _("Register with your phone number and confirm the code we send you. Then name your workspace and say "
            "whether you are a landlord, a company or an agency.")),
         (_("Add a property"),
          _("Open Properties and add its name and where it is. If you manage it for someone else, add the owner.")),
         (_("Add the units"),
          _("Add each unit with its rent. If you have many, skip to the tutorial on bringing in a spreadsheet.")),
         (_("Add a tenant"), _("Open Tenants and add the name. A phone number is enough to start.")),
         (_("Start the lease"),
          _("From the unit page, start a lease: the tenant, start date, rent, due day and deposit.")),
         (_("Choose how tenants pay"),
          _("Connect your Paybill or Till (see the M-Pesa tutorial), or add a payment instruction such as a "
            "bank account for tenants to see.")),
         (_("Issue the invoice"),
          _("Invoices are made a few days before each month. To see one now, press Generate on Invoices.")),
     ]},
    {"slug": "import-spreadsheet", "title": _("Bring your records in from a spreadsheet"), "minutes": 15,
     "summary": _("Load units, tenants and what tenants already owe, with every row checked first."),
     "help": "importing",
     "steps": [
         (_("Open Import"),
          _("Choose Import from the menu and pick what you are loading: units, tenants or balances.")),
         (_("Download the template"), _("Use the CSV template for that list so the columns match.")),
         (_("Fill it in"), _("Use Excel or Google Sheets, then save the file as CSV.")),
         (_("Upload and check"), _("Nothing is saved yet. Every row is shown with any problems beside it.")),
         (_("Import the good rows"), _("Save the rows that are fine, then fix the rest and upload them again.")),
         (_("Do them in order"),
          _("Units first, then tenants, then opening balances for what tenants already owe.")),
     ]},
    {"slug": "connect-mpesa", "title": _("Receive rent on your own Paybill"), "minutes": 30,
     "summary": _("Connect your shortcode so payments are matched to units automatically."),
     "help": "mpesa",
     "steps": [
         (_("Decide Paybill or Till"),
          _("A Paybill matches payments by account number. A Till cannot, so each payment waits for you to match it.")),
         (_("Make a Daraja app"),
          _("On Safaricom's Daraja portal create an app, and ask Safaricom to put your shortcode live on it.")),
         (_("Enter your details"),
          _("On M-Pesa settings add the shortcode, consumer key and secret, and for a Paybill the passkey.")),
         (_("Register the payment links"), _("Press the button on the same page so Safaricom sends us each payment.")),
         (_("Tell your tenants"),
          _("Each unit has a payment reference. Tenants use it as the account number when they pay.")),
         (_("Watch the first payment arrive"),
          _("A payment with the right reference is recorded and receipted at once. Others wait in the M-Pesa "
            "inbox for you to match.")),
     ]},
    {"slug": "record-payments", "title": _("Record a cash or bank payment"), "minutes": 5,
     "summary": _("Take a payment by hand and give the tenant a receipt."),
     "help": "invoices-payments",
     "steps": [
         (_("Open Payments"), _("Choose Payments and add a payment.")),
         (_("Pick the lease and amount"), _("Choose the tenant's lease, the amount, the method and the date.")),
         (_("Save"),
          _("It goes against the oldest unpaid invoice first. If your role cannot confirm payments, it waits for "
            "someone who can.")),
         (_("Share the receipt"), _("Every confirmed payment has a numbered receipt and a link for the tenant.")),
         (_("Made a mistake?"), _("Nothing is deleted. Reverse the payment with a reason and record it again.")),
     ]},
    {"slug": "follow-up-arrears", "title": _("Chase arrears without awkward calls"), "minutes": 10,
     "summary": _("See who owes, who to call today, and send reminders."),
     "help": "invoices-payments",
     "steps": [
         (_("Open Arrears"), _("It lists who owes, how much, and for how long.")),
         (_("Use the call list"), _("The call list shows who to phone today and keeps notes of what they said.")),
         (_("Let reminders go out"),
          _("Rent reminders and overdue notices are sent by SMS on their own. Quiet hours are respected.")),
         (_("Consider late fees"),
          _("Switch them on in Settings if you want them. Each fee waits for you to charge or skip it.")),
     ]},
    {"slug": "meter-readings", "title": _("Bill water or electricity by meter"), "minutes": 15,
     "summary": _("Add a meter, take a monthly round on your phone, and bill what was used."),
     "help": "water",
     "steps": [
         (_("Add the meter"),
          _("On Meters choose water or electricity, the units it serves and the rate per m³ or kWh.")),
         (_("Take the first reading"), _("It is a starting point and bills nothing.")),
         (_("Do the monthly round"),
          _("Open the round page before you go in. With no signal, readings are kept on the phone and sent when "
            "you are back online.")),
         (_("Approve"), _("Someone who may approve checks the readings. Unusual ones are flagged and need a note.")),
         (_("It is billed"), _("Approved readings go on next month's invoice, with the readings shown.")),
     ]},
    {"slug": "invite-your-team", "title": _("Invite your caretaker or accountant"), "minutes": 5,
     "summary": _("Give each person only what they need."),
     "help": "staff-roles",
     "steps": [
         (_("Open Staff"), _("Choose Invite and enter their phone number.")),
         (_("Choose a role"),
          _("Manager, Accountant, Caretaker, Leasing agent, Maintenance or Viewer. You can copy a role to make "
            "your own.")),
         (_("Limit the properties"), _("Pick the properties or branches they can see, or leave it to all.")),
         (_("They accept"), _("They get an SMS link and choose a password. Ask them to switch on two-step login.")),
     ]},
]
SLUG_TO_TUTORIAL = {t["slug"]: t for t in TUTORIALS}


def tutorial(slug: str):
    return SLUG_TO_TUTORIAL.get(slug)
