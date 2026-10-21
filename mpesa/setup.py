"""The guided Paybill/Till setup: where the landlord is, what to do next, and how to ask us for help (D-174).

Nothing here changes data. The steps are worked out from what is already stored, so they never drift.
"""

import re
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.utils.translation import gettext as _

from payments.models import PaymentAccount, PropertyPaymentAccount

from .models import DarajaCredentials, MpesaTransaction, SetupVideo


def whatsapp_number() -> str:
    from core.contacts import support_whatsapp

    return support_whatsapp()


def video_url() -> str:
    """The link set in Django admin; the environment value is only a fallback while admin's is empty."""
    url = SetupVideo.objects.filter(pk=1).values_list("url", flat=True).first() or ""
    url = url.strip() or getattr(settings, "MPESA_SETUP_VIDEO_URL", "").strip()
    return url if url.startswith("https://") else ""


def video_embed() -> str:
    """The privacy-friendly YouTube embed address for the setup video, or empty (other links open in a new tab)."""
    url = video_url()
    match = re.match(r"https://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/)|youtu\.be/)"
                     r"([A-Za-z0-9_-]{11})", url)
    return f"https://www.youtube-nocookie.com/embed/{match.group(1)}" if match else ""


def friendly_error(raw: str) -> str:
    """Safaricom's own words turned into what to do. Unknown errors keep the original text."""
    low = (raw or "").lower()
    if "could not reach" in low or "timed out" in low or "not json" in low:
        return _("We could not reach Safaricom just now. Try again in a few minutes.")
    if "access token" in low or "401" in low or "consumer key" in low or "invalid credentials" in low:
        return _("Safaricom did not accept the consumer key and secret. Copy them again from your Daraja app. "
                 "Live keys only work with “Live”, and sandbox keys only with “Sandbox”.")
    if "shortcode" in low or "short code" in low:
        return _("Safaricom does not know this number on your app. Check that the Paybill number is the one "
                 "Safaricom approved for your app.")
    if "duplicate" in low or "already" in low:
        return _("Safaricom says addresses are already registered for this number. Message us and we will "
                 "sort it out with you.")
    if "403" in low or "forbidden" in low or "not authori" in low:
        return _("Your Daraja app is not yet approved for live payments on this number. Finish Go-Live with "
                 "Safaricom first.")
    return raw


def help_link(*, organization, user, account: PaymentAccount | None = None, step: str = "") -> str:
    """A WhatsApp link with the message already written. Empty when no support number is set."""
    number = whatsapp_number()
    if not number:
        return ""
    lines = [_("Hello NyumbaPMS, I need help connecting my Paybill/Till."),
             _("Organization: %(name)s") % {"name": organization.name}]
    name = (getattr(user, "get_full_name", lambda: "")() or "").strip()
    if name:
        lines.append(_("My name: %(name)s") % {"name": name})
    if account is not None:
        lines.append(_("Account: %(type)s %(number)s") % {"type": account.get_type_display(), "number": account.number})
    if step:
        lines.append(_("I am at: %(step)s") % {"step": step})
    return f"https://wa.me/{number}?text={quote(chr(10).join(lines))}"


def steps(account: PaymentAccount, creds: DarajaCredentials | None) -> list[dict]:
    """The setup steps in order, each with a state: done, current or todo, and the plain-language how-to."""
    live = bool(creds) and creds.environment == DarajaCredentials.Environment.PRODUCTION
    keys = bool(creds) and creds.can_connect
    connected = bool(creds) and creds.urls_registered_at is not None
    properties = PropertyPaymentAccount.objects.filter(payment_account=account).count()
    test = None
    if connected:
        test = (MpesaTransaction.objects.filter(payment_account=account, created_at__gte=creds.urls_registered_at)
                .order_by("created_at").first())
    paid = test is not None
    paybill = account.type == PaymentAccount.Type.PAYBILL

    waiting = account.safaricom_applied_at is not None and not (live and keys)
    rows = [
        {"key": "account", "done": True, "title": _("Add your Paybill or Till"),
         "body": _("Done. This is the number tenants pay into. The money goes straight to you.")},
        {"key": "daraja", "done": keys,
         "title": _("Make a free Daraja account and app (on Safaricom's site)"),
         "body": _("Daraja is Safaricom's website for business software. Go to developer.safaricom.co.ke, sign "
                   "up with your email, then open My Apps and add a new app. Tick the products for payments to "
                   "a Paybill (C2B)%(stk)s. You do this once.")
                 % {"stk": _(" and M-Pesa Express (STK Push) for payment requests") if paybill else ""}},
        {"key": "golive", "done": live and keys,
         "title": (_("Waiting for Safaricom to approve your app")
                   if waiting else _("Ask Safaricom to make your app live (Go-Live)")),
         "waiting": waiting, "applied_at": account.safaricom_applied_at,
         "body": _("Safaricom checks that you own the number before real money can flow. On the Daraja site, "
                   "open Go Live and apply for your Paybill. They may ask for your business or ID documents and "
                   "KRA PIN. Approval can take a few days; you will get your live keys, and for a Paybill a "
                   "passkey, from them. While you wait, you can still use the app by recording payments by hand.")},
        {"key": "keys", "done": keys and live,
         "title": _("Paste your live keys here"),
         "body": _("Choose Live, then paste the consumer key and secret from your approved app. Add the passkey "
                   "too: without it you cannot send payment requests or pay links. We check the keys the moment "
                   "you save, and keep them encrypted.")},
        {"key": "connect", "done": connected,
         "title": _("Connect to Safaricom"),
         "body": _("One press tells Safaricom to send every payment to this number straight here. “Save and "
                   "connect” does it for you.")},
        {"key": "properties", "done": properties > 0,
         "title": _("Choose the properties it collects for"),
         "body": _("Pick which of your properties are paid into this number. Tenants see it on their invoices.")},
        {"key": "payment", "done": paid, "test": test,
         "title": _("Send a KES 10 test payment"),
         "body": _("From your own phone, pay KES 10 to %(number)s with a unit's payment reference as the account "
                   "number. We watch for it: this step turns green when it arrives.") % {"number": account.number},
         "trouble": _("Nothing yet? Check that you paid the right number (%(number)s) and the right account "
                      "number, that the keys are Live and not Sandbox, and that the connection above says "
                      "registered. Payments can take a minute to arrive.") % {"number": account.number}},
    ]
    current = next((r for r in rows if not r["done"]), None)
    for i, r in enumerate(rows, 1):
        r["n"] = i
        r["state"] = "done" if r["done"] else ("current" if r is current else "todo")
    return rows


def progress(account: PaymentAccount, creds: DarajaCredentials | None) -> dict:
    rows = steps(account, creds)
    current = next((r for r in rows if r["state"] == "current"), None)
    done = sum(1 for r in rows if r["done"])
    return {"steps": rows, "current": current, "done": done, "total": len(rows), "complete": current is None}


# -- Reminders -------------------------------------------------------------------------------------

REMIND_AFTER_DAYS = 3  # then every week, until the account is two months old
STOP_AFTER_DAYS = 60


def send_reminders(now) -> int:
    """In-app, to each `mpesa.settings` holder, for Paybills and Tills added a while ago and not connected.

    One reminder per account, person and week (a dedupe key), so a rerun the same day sends nothing."""
    from accounts.models import Membership
    from accounts.permissions import can
    from notifications.delivery import notify

    sent = 0
    accounts = (PaymentAccount.objects.filter(type__in=(PaymentAccount.Type.PAYBILL, PaymentAccount.Type.TILL),
                                              created_at__lte=now - timedelta(days=REMIND_AFTER_DAYS),
                                              created_at__gt=now - timedelta(days=STOP_AFTER_DAYS))
                .select_related("organization", "daraja"))
    for account in accounts:
        creds = getattr(account, "daraja", None)
        if creds is not None and creds.urls_registered_at:
            continue
        prog = progress(account, creds)
        step = prog["current"]["title"] if prog["current"] else ""
        if not step:
            continue
        if prog["current"]["key"] == "golive" and prog["current"].get("waiting"):
            step = _("%(step)s. If you have heard from Safaricom, paste your live keys. If it has been more than "
                     "a week, message us") % {"step": step}
        stage = (now - account.created_at).days // 7
        members = (Membership.objects.filter(organization=account.organization, is_active=True, user__is_active=True)
                   .select_related("user", "organization", "role"))
        for m in members:
            if not can(m, "mpesa.settings"):
                continue
            if notify(account.organization, "mpesa_setup_stalled", user=m.user,
                      context={"account": f"{account.get_type_display()} {account.number}", "step": step},
                      dedupe_key=f"mpesa_setup:{account.pk}:{m.user.pk}:{stage}", now=now):
                sent += 1
    return sent
