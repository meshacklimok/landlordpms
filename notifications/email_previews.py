"""Sample emails for the console's preview page: the real renderer, made-up words, so a layout change can be seen."""

from .email_html import render_html

SAMPLES = [
    ("sign-in", "Sign-in code", "NyumbaPMS", "Open",
     "Your NyumbaPMS code:\n\n482913\n\nIt expires in 10 minutes. Never share it."),
    ("invoice", "Rent invoice (to a tenant)", "Sample Estates", "Pay now",
     "Hello Grace,\n\nYour rent invoice for November is ready. The PDF is attached.\n\n"
     "Invoice: INV-2026-0142\nUnit: B4, Sunrise Apartments\nAmount: KES 18,500\nDue: 5 Nov 2026\n\n"
     "Pay with account number B4.\nhttps://app.nyumbapms.com/p/sample\n\n"
     "Thank you,\nSample Estates\n-- \nYou get this email because you rent from Sample Estates. "
     "To stop these emails, open: https://app.nyumbapms.com/messages/stop/sample"),
    ("receipt", "Payment received", "Sample Estates", "Open",
     "Hello Grace,\n\nWe received your payment. Thank you.\n\n"
     "Receipt: RCT-2026-0391\nAmount: KES 18,500\nM-Pesa code: SJK4L2M9QX\nBalance: KES 0\n\n"
     "Your receipt is attached.\n-- \nYou get this email because you rent from Sample Estates. "
     "To stop these emails, open: https://app.nyumbapms.com/messages/stop/sample"),
    ("reminder", "Rent reminder", "Sample Estates", "Pay now",
     "Hello Grace,\n\nA gentle reminder: rent of KES 18,500 for unit B4 is due on 5 Nov 2026.\n\n"
     "Pay with account number B4.\nhttps://app.nyumbapms.com/p/sample\n\nIf you have already paid, thank you, and please ignore this email.\n"
     "-- \nYou get this email because you rent from Sample Estates. "
     "To stop these emails, open: https://app.nyumbapms.com/messages/stop/sample"),
    ("owner-invite", "Owner portal invitation", "Sample Estates", "Open your owner portal",
     "Hello Joseph,\n\nSample Estates has set up a portal where you can see your properties, "
     "statements and payouts.\n\nhttps://app.nyumbapms.com/owner/accept/abc123\n\n"
     "Open this link within 7 days and log in with your phone number 0712 345 678.\n\nSample Estates"),
    ("statement", "Owner statement", "Sample Estates", "Open",
     "Dear Joseph,\n\nPlease find attached your statement for October 2026 (OS-2026-0017).\n\n"
     "Collected: KES 412,000\nManagement fee: KES 41,200\nExpenses: KES 18,750\nDue to you: KES 352,050\n"
     "Paid to you: KES 300,000\nStill to pay: KES 52,050\n\nSample Estates"),
    ("demo", "Demo request received", "NyumbaPMS", "Read the tutorials",
     "Hello Wanjiru,\n\nThank you for asking for a NyumbaPMS demo. We will contact you on 0722 000 111 to agree a "
     "time. You asked for tomorrow morning.\n\nUntil then you can read the tutorials at:\n\n"
     "https://app.nyumbapms.com/tutorials/"),
    ("announcement", "Message from NyumbaPMS to owners", "NyumbaPMS", "Open",
     "Hello Peter,\n\nWe have added late fees. You can now set a fixed fee or a percentage with a cap, and "
     "choose to charge or skip each one.\n\nhttps://app.nyumbapms.com/billing/late-fees/\n"
     "-- \nYou get this email because Sample Estates uses NyumbaPMS."),
]


def keys() -> list[str]:
    return [s[0] for s in SAMPLES]


def render(key: str) -> str | None:
    for k, _label, org, button, body in SAMPLES:
        if k == key:
            return render_html(org, body, button_label=button)
    return None


def send_test(key: str, to: str) -> bool:
    """Sends one sample to `to` through the mail account that handles tenant mail. False when the key is unknown."""
    from .email_html import send_branded
    from .providers.mail import MESSAGES, mailer_for

    for k, label, org, button, body in SAMPLES:
        if k == key:
            send_branded("[Test] " + label, body, [to], using=mailer_for(MESSAGES), org_name=org, button_label=button)
            return True
    return False
