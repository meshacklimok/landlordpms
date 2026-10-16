"""Pages for the things Kenyan landlords actually search for (D-158): M-Pesa rent collection, software for agents,
estate service charge, meter billing, arrears, and landlords abroad. Each answers one question in full instead of
repeating the home page. Only what is built is claimed; no prices (the pricing page reads them from the plans), no
customer numbers, no legal advice."""

from django.utils.translation import gettext_lazy as _

# slug, title (the <title>, short), h1, description (<=160 characters), lead, sections [(heading, text, [points])],
# faq [(question, answer)], related [slug], tutorial (slug of a tutorial that shows the steps)
SOLUTIONS = [
    {"slug": "mpesa-rent-collection",
     "title": _("M-Pesa rent collection software"),
     "h1": _("Collect rent by M-Pesa and know who paid, instantly"),
     "description": _("Tenants pay rent to your own Paybill or Till. NyumbaPMS matches each M-Pesa payment to the "
                      "right unit, sends a receipt by SMS and shows who still owes."),
     "lead": _("Stop scrolling through M-Pesa messages. Tenants pay into your own Paybill or Till; each payment is "
               "matched to a unit, receipted and counted against the oldest unpaid invoice."),
     "sections": [
         (_("Your Paybill or Till, your money"),
          _("Rent goes straight from the tenant to your own shortcode. NyumbaPMS never holds it. If you do not have a "
            "Paybill yet, you can still record M-Pesa, cash and bank payments by hand."),
          [_("Paybill and Till both work; a Paybill saves the most matching work because tenants type the unit number as the account."),
           _("Tenants can also be sent a link to pay from their phone by M-Pesa prompt."),
           _("Payments that cannot be matched wait in one inbox for a single tap, instead of being lost.")]),
         (_("A receipt every time"),
          _("The moment a payment is matched the tenant gets a numbered receipt by SMS, with the balance left. "
            "That ends most arguments about whether a payment was made."),
          [_("Receipts are numbered and can be checked online by anyone holding the number."),
           _("Mistakes are reversed with a reason, never quietly deleted, so every figure can be checked later.")]),
         (_("See it all on one page"),
          _("Income against billed, arrears by tenant, and the list of who to call today."),
          []),
     ],
     "faq": [
         (_("Can tenants pay rent by M-Pesa without me checking my phone?"),
          _("Yes. When tenants pay to your Paybill, the payment is matched to their unit and a receipt goes out by SMS "
            "without you doing anything.")),
         (_("Do I need a Paybill?"),
          _("It is the easiest way, because the account number identifies the unit. With a Till or plain M-Pesa you "
            "can still record each payment, and matching takes a tap.")),
         (_("Does NyumbaPMS hold my tenants' rent?"),
          _("No. Rent is paid directly to your Paybill or Till.")),
     ],
     "related": ["rent-arrears-and-late-fees", "water-and-electricity-billing"], "tutorial": "mpesa"},

    {"slug": "software-for-agents-and-property-managers",
     "title": _("Software for rental agents and property managers"),
     "h1": _("Manage many landlords' properties from one login"),
     "description": _("Rent software for Kenyan agents and property managers: many owners, owner statements, your own "
                      "management fee, and staff who see only what you allow."),
     "lead": _("If you collect rent for other people, you need each owner's money kept apart, a statement you can "
               "send, and staff who can only do their own job."),
     "sections": [
         (_("Many owners, kept apart"),
          _("Add each owner and the properties you manage for them. Reports and statements can be shown owner by owner."),
          [_("Owners can be invited to a read-only page of their own; tenant names are hidden from them."),
           _("Branches can be given their own access.")]),
         (_("Your fee on the record"),
          _("Set your management fee and see what you earned each month next to what you collected for owners."),
          []),
         (_("Staff with limited access"),
          _("Give a caretaker, accountant or branch manager only the access their work needs, and see in the audit "
            "log who did what."),
          [_("Roles are built from permissions, so a caretaker can take meter readings and nothing else.")]),
     ],
     "faq": [
         (_("Can my landlords see their own figures?"),
          _("Yes. An owner can be invited to a read-only page that shows collections and arrears for their properties.")),
         (_("Can I limit what my staff see?"),
          _("Yes. Each team member gets a role made of permissions, and every action is written to an audit log.")),
     ],
     "related": ["mpesa-rent-collection", "manage-rental-property-in-kenya-from-abroad"], "tutorial": "staff"},

    {"slug": "estate-service-charge-and-water-billing",
     "title": _("Service charge and water billing for estates"),
     "h1": _("Service charge, water and garbage on one invoice per household"),
     "description": _("Billing for gated estates, apartments and water services in Kenya: service charge, metered water "
                      "and garbage on a single invoice, paid by M-Pesa."),
     "lead": _("Estates and water services bill hundreds of households every month for several different things. "
               "Put them on one invoice and let each household pay in one go."),
     "sections": [
         (_("Recurring charges, automatically"),
          _("Service charge, garbage and security are added to every invoice without retyping them."),
          []),
         (_("Water by meter"),
          _("Staff read meters on a phone, even with no signal, and readings are approved before they are billed."),
          [_("Water can be metered, a flat charge, or included in the rent, set property by property."),
           _("A reading sheet and QR labels for each meter make the round faster.")]),
         (_("Households pay and see their own balance"),
          _("Each household pays to the estate's Paybill with its own reference and can look up its balance by a "
            "short code on a basic phone."),
          []),
     ],
     "faq": [
         (_("Can one invoice include rent, water and service charge?"),
          _("Yes. Charges for the month are added to the same invoice, so the household pays once.")),
         (_("What if a caretaker has no data bundle?"),
          _("Meter readings can be typed without a connection and are sent when the phone is online again.")),
     ],
     "related": ["water-and-electricity-billing", "mpesa-rent-collection"], "tutorial": "meters"},

    {"slug": "water-and-electricity-billing",
     "title": _("Water and electricity meter billing"),
     "h1": _("Bill water and electricity from meter readings"),
     "description": _("Take meter readings on a phone, approve them and bill what each tenant used with the rent. "
                      "Works offline for caretakers in Kenyan plots and flats."),
     "lead": _("Replace the notebook of readings. Take each reading on a phone, check it, and the bill is added to "
               "the tenant's invoice."),
     "sections": [
         (_("Readings that are checked"),
          _("Odd readings are flagged before they become a bill, and a person approves each batch."),
          [_("Take a photo of the meter with each reading."),
           _("Shared meters can be split between units by a percentage.")]),
         (_("Works without signal"),
          _("A caretaker can finish the whole round offline; the readings are sent when the phone is back online."),
          []),
         (_("Billed with the rent"),
          _("The charge appears on the same invoice and is receipted with the rent payment."),
          []),
     ],
     "faq": [
         (_("Can tenants dispute a reading?"),
          _("Readings keep their photo and history, so a disputed figure can be checked and, if wrong, reversed "
            "with a reason.")),
         (_("Is the water price per unit set by me?"),
          _("Yes. You set the rate for each property.")),
     ],
     "related": ["estate-service-charge-and-water-billing", "mpesa-rent-collection"], "tutorial": "meters"},

    {"slug": "rent-arrears-and-late-fees",
     "title": _("Rent arrears and late fee management"),
     "h1": _("Chase rent arrears without awkward calls"),
     "description": _("See who owes, send SMS reminders before and after the due date, apply the late fee you agreed "
                      "in the lease, and keep a record of every notice served."),
     "lead": _("Late rent is easier to deal with when it is noticed on day one, the tenant is reminded politely, "
               "and everything you sent is on record."),
     "sections": [
         (_("Reminders that run themselves"),
          _("Invoices, reminders and overdue notices go out by SMS, with quiet hours, and the arrears page lists who "
            "to phone today."),
          []),
         (_("Late fees you decide on"),
          _("Set a fixed fee or a percentage with a cap. The system proposes fees each day; staff charge or skip each "
            "one."),
          []),
         (_("Notices and a record"),
          _("Demand letters and notices to vacate are made from the tenant's own records, with a service record of "
            "when and how each was delivered."),
          [_("If a matter goes to an advocate, a handover pack gathers the statement and the letters as served.")]),
     ],
     "faq": [
         (_("Are late fees charged automatically?"),
          _("No. A person confirms each one, so you stay in control and can be fair.")),
         (_("Is this legal advice?"),
          _("No. NyumbaPMS keeps the records; talk to an advocate about your rights and notices.")),
     ],
     "related": ["mpesa-rent-collection", "software-for-agents-and-property-managers"], "tutorial": "first-invoice"},

    {"slug": "manage-rental-property-in-kenya-from-abroad",
     "title": _("Manage Kenyan rental property from abroad"),
     "h1": _("Own rental property in Kenya? Manage it from anywhere"),
     "description": _("For Kenyans in the diaspora: see rent collected, arrears, vacancies and repair photos on your "
                      "phone, without calling the caretaker every month."),
     "lead": _("When you live in the UK, US, Canada, UAE or Australia, you need proof that the rent arrived and the "
               "building is cared for, without depending on a phone call."),
     "sections": [
         (_("Collections you can see"),
          _("Every M-Pesa payment is matched to a unit and receipted. You see income, arrears and vacancies on one "
            "page, in shillings."),
          []),
         (_("Your caretaker, with limits"),
          _("Give the caretaker only the access they need, such as meter readings and repair updates, and see what "
            "they did."),
          []),
         (_("Repairs with photos"),
          _("Repair reports come with photos and status, so you approve work with the facts in front of you."),
          []),
     ],
     "faq": [
         (_("Can I use NyumbaPMS from outside Kenya?"),
          _("Yes. It runs in your browser and installs on a phone like an app. Tenants still pay by M-Pesa in Kenya.")),
         (_("Can I give my agent access instead of the caretaker?"),
          _("Yes. Invite an agent, or an owner view that shows figures without tenants' names.")),
     ],
     "related": ["software-for-agents-and-property-managers", "mpesa-rent-collection"], "tutorial": "first-invoice"},

    {"slug": "water-vendor-billing-software",
     "title": _("Billing software for water vendors"),
     "h1": _("Bill water customers by meter and collect by M-Pesa"),
     "description": _("For water vendors and suppliers: bill each customer from meter readings, record supply cuts and "
                      "restorations, and collect by M-Pesa with a receipt every time."),
     "lead": _("Keep every customer's readings, bills and payments in one place instead of a notebook, and see who "
               "has not paid."),
     "sections": [
         (_("Billed from readings"),
          _("Take each meter reading on a phone, check it, and the bill is added to the customer's invoice."),
          [_("Odd readings are flagged before they become a bill."),
           _("Readings keep their photo and history, so a disputed figure can be checked.")]),
         (_("Supply days and cuts"),
          _("Record the days water is supplied for each property, and mark a customer's supply as cut or restored. "
            "The customer can see the date. Billing and payments carry on."),
          []),
         (_("Paid by M-Pesa"),
          _("Customers pay to your own Paybill or Till; each payment is matched to the account and receipted by SMS."),
          []),
     ],
     "faq": [
         (_("Does it cut the supply automatically?"),
          _("No. You mark the supply as cut or restored yourself; the system records the date and shows it to the "
            "customer.")),
         (_("Can customers report a fault?"),
          _("Yes. They can report water supply, plumbing or meter problems from their portal.")),
     ],
     "related": ["water-and-electricity-billing", "mpesa-rent-collection"], "tutorial": "meters"},

    {"slug": "garbage-collection-billing-software",
     "title": _("Billing software for garbage collectors"),
     "h1": _("Bill garbage collection per pickup or per month"),
     "description": _("For garbage collection companies: record each pickup as collected or missed on a day sheet, "
                      "bill only collected pickups, and collect by M-Pesa."),
     "lead": _("Mark each client on your route as collected or missed, and bill what was actually collected."),
     "sections": [
         (_("A day sheet for the route"),
          _("Open the day sheet for a property, mark each contract as collected or missed, or mark the rest "
            "collected in one tap. Days up to about two months back can be corrected."),
          []),
         (_("Billed per pickup"),
          _("A charge can be the price of one pickup. The invoice for a month bills the pickups collected in the "
            "month before it; missed pickups are not charged and are listed for each contract."),
          []),
         (_("Paid by M-Pesa"),
          _("Clients pay to your own Paybill or Till; each payment is matched and receipted by SMS."),
          []),
     ],
     "faq": [
         (_("Can I also charge a fixed monthly fee?"),
          _("Yes. A contract can have a fixed charge as well as, or instead of, a per-pickup one.")),
         (_("Can clients report a missed pickup?"),
          _("Yes. They can report a missed pickup or a cleaning request from their portal.")),
     ],
     "related": ["mpesa-rent-collection", "rent-arrears-and-late-fees"], "tutorial": "first-invoice"},
]

SLUG_TO_SOLUTION = {s["slug"]: s for s in SOLUTIONS}


def solution(slug: str):
    return SLUG_TO_SOLUTION.get(slug)
