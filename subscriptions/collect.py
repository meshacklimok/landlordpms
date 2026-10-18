"""Landlords paying their subscription online, into our own accounts (D-126).

Two ways, each offered when its keys are set and it is switched on (PlatformPaySwitch):

1. **Our Paybill, through Daraja.** "Pay with M-Pesa" sends a prompt to the landlord's phone (STK push);
   Safaricom's callback names the request, so the payment goes on that invoice. A landlord who pays the
   Paybill by hand types the invoice's account number (`account_number`), and the C2B confirmation is
   matched by it.
2. **Our Paystack account.** "Pay with card or M-Pesa" opens Paystack's checkout page. Paystack's signed
   webhook, or the verify call when the landlord comes back, settles it.

Every payment is stored once per code as a PlatformTransaction, so a prompt's callback and its C2B
confirmation (or a webhook and a verify) count once. One that cannot go on an open invoice waits as
UNMATCHED for a Platform Admin, who records it by hand with the same code. Nothing here touches
landlords' own M-Pesa accounts, rent or tenants (the `mpesa` app); only the HTTP clients are shared.
"""

import datetime
import hashlib
import logging
import math
import re
import uuid
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import dateparse, timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import require
from audit import services as audit
from core.crypto import decrypt, encrypt
from core.phone import InvalidPhoneNumber, normalize_phone
from mpesa import c2b as mpesa_c2b
from mpesa import daraja
from mpesa import stk as mpesa_stk
from mpesa.models import FORBIDDEN_URL_WORDS
from notifications.providers.mail import SYSTEM, mailer_for
from subscriptions import paystack

from . import services
from .models import PlatformCheckout, PlatformPaySwitch, PlatformTransaction, SubscriptionInvoice, SubscriptionPayment

logger = logging.getLogger(__name__)

Provider = PlatformCheckout.Provider
Source = PlatformTransaction.Source
MAX_PROMPT = Decimal("250000")  # M-Pesa's limit per transaction [VERIFY]
RESEND_AFTER = datetime.timedelta(minutes=2)
# A request with no answer by then is closed by the daily job.
GIVE_UP_AFTER = datetime.timedelta(hours=24)
_NOT_ALNUM = re.compile(r"[^0-9A-Za-z]")


class BadCallback(ValueError):
    """The callback cannot be read, so nothing is stored."""


# ---------------------------------------------------------------------------
# Our accounts, shaped like the landlords' credentials so the mpesa clients work unchanged
# ---------------------------------------------------------------------------


class PlatformDaraja:
    """Our Paybill's Daraja keys, from the environment. `pk` keys the cached OAuth token."""

    pk = "platform"

    def __init__(self):
        self.environment = settings.PLATFORM_DARAJA_ENVIRONMENT
        self.shortcode = settings.PLATFORM_DARAJA_SHORTCODE
        self._secrets = {"consumer_key": settings.PLATFORM_DARAJA_CONSUMER_KEY,
                         "consumer_secret": settings.PLATFORM_DARAJA_CONSUMER_SECRET,
                         "passkey": settings.PLATFORM_DARAJA_PASSKEY}
        # The client hashes this into the token's cache key: new keys never reuse an old token.
        self.consumer_key_encrypted = hashlib.sha256(self._secrets["consumer_key"].encode()).hexdigest()

    def secret(self, name: str) -> str:
        return self._secrets[name]


class PlatformPaystack:
    pk = "platform"

    def secret(self, name: str = "secret_key") -> str:
        return settings.PLATFORM_PAYSTACK_SECRET_KEY


def paybill() -> str:
    """The Paybill landlords pay by hand, shown on the page and the invoice PDF."""
    return settings.PLATFORM_PAYBILL or settings.PLATFORM_DARAJA_SHORTCODE


def mpesa_configured() -> bool:
    return bool(settings.PLATFORM_DARAJA_CONSUMER_KEY and settings.PLATFORM_DARAJA_CONSUMER_SECRET
                and settings.PLATFORM_DARAJA_SHORTCODE and settings.PLATFORM_CALLBACK_TOKEN)


def prompt_configured() -> bool:
    return mpesa_configured() and bool(settings.PLATFORM_DARAJA_PASSKEY)


def paystack_configured() -> bool:
    return bool(settings.PLATFORM_PAYSTACK_SECRET_KEY and settings.PLATFORM_CALLBACK_TOKEN)


def methods() -> dict[str, bool]:
    """What the subscription page offers now."""
    switch = PlatformPaySwitch.get()
    return {"mpesa": switch.mpesa_on and prompt_configured(),
            "paystack": switch.paystack_on and paystack_configured()}


def callback_url(kind: str) -> str:
    return settings.SITE_URL + reverse("hook_platform", args=[settings.PLATFORM_CALLBACK_TOKEN, kind])


def check_callback_url(kind: str) -> str:
    url = callback_url(kind)
    live = settings.PLATFORM_DARAJA_ENVIRONMENT == "PRODUCTION"
    if live and not url.startswith("https://"):
        raise ValidationError(_("Live M-Pesa needs the site on https. Set SITE_URL to the https address."))
    word = next((w for w in FORBIDDEN_URL_WORDS if w in url.lower()), None)
    if word:
        raise ValidationError(_("Safaricom refuses callback addresses containing “%(word)s”. Change SITE_URL.")
                              % {"word": word})
    return url


# ---------------------------------------------------------------------------
# Invoices' account numbers
# ---------------------------------------------------------------------------


def account_number(invoice: SubscriptionInvoice) -> str:
    """What a landlord types as the Paybill account: the invoice number's digits, 2026000123. It fits
    M-Pesa's 12-character account reference; the full number (LPM-INV-2026-000123) is accepted too."""
    return re.sub(r"\D", "", invoice.number)[-12:]


def invoice_for_account(typed: str) -> SubscriptionInvoice | None:
    """The open invoice whose number, or its digits, is what the payer typed."""
    typed = _NOT_ALNUM.sub("", typed or "").upper()
    if not typed:
        return None
    digits = re.sub(r"\D", "", typed)
    for invoice in SubscriptionInvoice.objects.filter(status=SubscriptionInvoice.Status.OPEN,
                                                      number__endswith=digits[-6:] if digits else typed):
        if typed in (_NOT_ALNUM.sub("", invoice.number).upper(), account_number(invoice)):
            return invoice
    return None


# ---------------------------------------------------------------------------
# Starting a payment
# ---------------------------------------------------------------------------


def _payable(actor: Membership, invoice: SubscriptionInvoice) -> Decimal:
    require(actor, "subscription.manage")
    if invoice.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    if invoice.status != SubscriptionInvoice.Status.OPEN or invoice.outstanding <= 0:
        raise ValidationError(_("That invoice has nothing left to pay."))
    return invoice.outstanding


SMS_ACCOUNT = "SMS"


def _audit(actor, checkout: PlatformCheckout, request, **extra) -> None:
    audit.record("subscription_checkout.start", actor=actor.user, organization=actor.organization, obj=checkout,
                 request=request, changes={"invoice": checkout.invoice.number if checkout.invoice else SMS_ACCOUNT,
                                           "provider": checkout.provider, "amount": str(checkout.amount), **extra})


def _top_up_amount(actor: Membership, amount) -> Decimal:
    require(actor, "subscription.manage")
    try:
        amount = Decimal(str(amount).replace(",", "").strip())
    except InvalidOperation:
        amount = None
    if amount is None or amount != amount.to_integral_value() or not (
            services.SMS_TOP_UP_MIN <= amount <= services.SMS_TOP_UP_MAX):
        raise ValidationError(_("Enter whole shillings from %(min)s to %(max)s.")
                              % {"min": f"{services.SMS_TOP_UP_MIN:,.0f}", "max": f"{services.SMS_TOP_UP_MAX:,.0f}"})
    return amount


def _prompt(actor: Membership, invoice: SubscriptionInvoice | None, amount: Decimal, phone: str,
            request) -> PlatformCheckout:
    """Sends the M-Pesa prompt and keeps the request; `invoice` is None for an SMS top-up."""
    if not methods()["mpesa"]:
        raise ValidationError(_("Paying by M-Pesa prompt is not available right now."))
    try:
        phone = normalize_phone(phone)
    except InvalidPhoneNumber:
        raise ValidationError(_("Enter a valid phone number.")) from None
    if not phone.startswith("+254"):
        raise ValidationError(_("M-Pesa prompts go to Kenyan numbers only."))
    if amount > MAX_PROMPT:
        raise ValidationError(_("That is more than one M-Pesa payment can carry. Pay by Paybill or card."))
    if PlatformCheckout.objects.filter(organization=actor.organization, invoice=invoice, provider=Provider.MPESA,
                                       status=PlatformCheckout.Status.PENDING,
                                       created_at__gte=timezone.now() - RESEND_AFTER).exists():
        raise ValidationError(_("A prompt was sent less than two minutes ago. Check your phone, or wait and "
                                "try again."))
    url = check_callback_url("stk")
    label = invoice.number if invoice else SMS_ACCOUNT
    try:
        data = daraja.get_client(PlatformDaraja()).stk_push(
            phone=phone, amount=int(amount), account_reference=account_number(invoice) if invoice else SMS_ACCOUNT,
            description="Subscription" if invoice else "SMS credit", callback_url=url)
    except daraja.DarajaError as e:
        logger.warning("Subscription M-Pesa prompt for %s refused: %s", label, e)
        raise ValidationError(_("Safaricom did not send the prompt: %(error)s") % {"error": e}) from None
    checkout = PlatformCheckout.objects.create(
        organization=actor.organization, invoice=invoice, provider=Provider.MPESA,
        reference=str(data["CheckoutRequestID"])[:80], phone=phone, amount=amount, created_by=actor.user)
    _audit(actor, checkout, request, phone=phone)
    return checkout


def _paystack_page(actor: Membership, invoice: SubscriptionInvoice | None, amount: Decimal, request,
                   save_card: bool = False) -> str:
    """Opens a Paystack checkout and returns the page to send the landlord to; `invoice` None is a top-up."""
    if not methods()["paystack"]:
        raise ValidationError(_("Paying by Paystack is not available right now."))
    org = actor.organization
    checkout = PlatformCheckout.objects.create(
        organization=org, invoice=invoice, provider=Provider.PAYSTACK, save_card=save_card,
        reference=f"nps-{uuid.uuid4().hex}", amount=amount, created_by=actor.user)
    email = ((invoice.customer_email if invoice else org.billing_email) or actor.user.email
             or f"billing-{org.pk}@{settings.PAYSTACK_EMAIL_DOMAIN}")
    label = invoice.number if invoice else SMS_ACCOUNT
    metadata = {"kind": "subscription", "invoice": invoice.number} if invoice else {"kind": "sms_top_up"}
    try:
        data = paystack.get_client(PlatformPaystack()).initialize(
            email=email, amount_cents=int(amount * 100), reference=checkout.reference,
            callback_url=settings.SITE_URL + reverse("subscriptions:paystack_return"), metadata=metadata)
    except paystack.PaystackError as e:
        logger.warning("Subscription Paystack checkout for %s refused: %s", label, e)
        raise ValidationError(_("Paystack could not start the payment: %(error)s") % {"error": e}) from None
    page = str(data.get("authorization_url") or "")
    if not page.startswith("https://"):
        raise ValidationError(_("Paystack could not start the payment. Try again."))
    _audit(actor, checkout, request)
    return page


def _paystack_prompt(actor: Membership, invoice: SubscriptionInvoice, amount: Decimal, phone: str,
                     request) -> PlatformCheckout:
    """Paystack sends the M-Pesa prompt: the way to pay when our own Daraja prompt is down."""
    if not methods()["paystack"]:
        raise ValidationError(_("Paying by Paystack is not available right now."))
    try:
        phone = normalize_phone(phone)
    except InvalidPhoneNumber:
        raise ValidationError(_("Enter a valid phone number.")) from None
    if not phone.startswith("+254"):
        raise ValidationError(_("M-Pesa prompts go to Kenyan numbers only."))
    if amount > MAX_PROMPT:
        raise ValidationError(_("That is more than one M-Pesa payment can carry. Pay by Paybill or card."))
    if PlatformCheckout.objects.filter(organization=actor.organization, invoice=invoice, provider=Provider.PAYSTACK,
                                       status=PlatformCheckout.Status.PENDING, phone__gt="",
                                       created_at__gte=timezone.now() - RESEND_AFTER).exists():
        raise ValidationError(_("A prompt was sent less than two minutes ago. Check your phone, or wait and "
                                "try again."))
    org = actor.organization
    email = invoice.customer_email or org.billing_email or actor.user.email or f"billing-{org.pk}@{settings.PAYSTACK_EMAIL_DOMAIN}"
    reference = f"nps-{uuid.uuid4().hex}"
    try:
        paystack.get_client(PlatformPaystack()).charge_mobile_money(
            email=email, amount_cents=int(amount * 100), reference=reference, phone=phone,
            metadata={"kind": "subscription", "invoice": invoice.number})
    except paystack.PaystackError as e:
        logger.warning("Subscription Paystack M-Pesa prompt for %s refused: %s", invoice.number, e)
        raise ValidationError(_("Paystack did not send the prompt: %(error)s") % {"error": e}) from None
    checkout = PlatformCheckout.objects.create(
        organization=org, invoice=invoice, provider=Provider.PAYSTACK, reference=reference, phone=phone,
        amount=amount, created_by=actor.user)
    _audit(actor, checkout, request, phone=phone)
    return checkout


@transaction.atomic
def start_paystack_mpesa(actor: Membership, invoice: SubscriptionInvoice, phone: str, *,
                         request=None) -> PlatformCheckout:
    """An M-Pesa prompt through Paystack for what is left on the invoice, in whole shillings rounded up."""
    invoice = SubscriptionInvoice.objects.select_for_update().get(pk=invoice.pk)
    return _paystack_prompt(actor, invoice, Decimal(math.ceil(_payable(actor, invoice))), phone, request)


@transaction.atomic
def start_mpesa(actor: Membership, invoice: SubscriptionInvoice, phone: str, *, request=None) -> PlatformCheckout:
    """Sends an M-Pesa prompt for what is left on the invoice, in whole shillings rounded up."""
    invoice = SubscriptionInvoice.objects.select_for_update().get(pk=invoice.pk)
    outstanding = _payable(actor, invoice)
    return _prompt(actor, invoice, Decimal(math.ceil(outstanding)), phone, request)


@transaction.atomic
def start_paystack(actor: Membership, invoice: SubscriptionInvoice, *, save_card: bool = False,
                   request=None) -> str:
    """Opens a Paystack checkout for what is left on the invoice. Returns the page to send the landlord to.
    With `save_card`, a card paid with is kept for automatic renewal."""
    invoice = SubscriptionInvoice.objects.select_for_update().get(pk=invoice.pk)
    return _paystack_page(actor, invoice, _payable(actor, invoice), request, save_card)


@transaction.atomic
def start_sms_mpesa(actor: Membership, amount, phone: str, *, request=None) -> PlatformCheckout:
    """An M-Pesa prompt to top up the SMS wallet (D-142)."""
    return _prompt(actor, None, _top_up_amount(actor, amount), phone, request)


@transaction.atomic
def start_sms_paystack(actor: Membership, amount, *, request=None) -> str:
    """A Paystack checkout to top up the SMS wallet (D-142)."""
    return _paystack_page(actor, None, _top_up_amount(actor, amount), request)


# ---------------------------------------------------------------------------
# Money coming in
# ---------------------------------------------------------------------------


def _alert(tx: PlatformTransaction) -> None:
    """Tells the people who run the platform about money that needs a person. Never fails the callback."""
    from ops.alerts import recipients

    to = recipients()
    if not to:
        return
    try:
        send_mail(f"[NyumbaPMS] Subscription payment {tx.code} needs a person",
                  f"{tx.get_source_display()} {tx.code}: KES {tx.amount:,.2f} from {tx.payer_name or tx.phone or '?'}, "
                  f"account '{tx.account}'.\n{tx.note}\n\nRecord it by hand in admin with reference {tx.code} "
                  "(an invoice payment or an SMS top-up); that clears it.",
                  None, to, using=mailer_for(SYSTEM))
    except Exception:
        logger.exception("Alert for subscription payment %s failed", tx.code)


def _record(*, source: str, code: str, amount: Decimal | None, paid_at, payload: dict, invoice=None,
            top_up_org=None, currency: str = "KES", account: str = "", phone: str = "", payer_name: str = "",
            problems: list[str] | None = None) -> tuple[PlatformTransaction, bool]:
    """Stores a payment once per code and puts it on `invoice`, or in `top_up_org`'s SMS wallet, if it can.
    Returns (transaction, created)."""
    problems = list(problems or [])
    if amount is None:
        problems.append("Amount could not be read.")
    if currency != "KES":
        problems.append(f"Paid in {currency}, not KES.")
    if invoice is None and top_up_org is None and not problems:
        problems.append("No open invoice has this account number.")
    try:
        with transaction.atomic():
            tx = PlatformTransaction.objects.create(
                source=source, code=code, amount=amount or 0, currency=currency, account=account[:60],
                phone=phone[:80], payer_name=payer_name[:150], paid_at=paid_at, invoice=invoice,
                status=PlatformTransaction.Status.UNMATCHED, raw_payload=payload)
    except IntegrityError:
        return PlatformTransaction.objects.get(code=code), False
    if not problems:
        method = SubscriptionPayment.Method.PAYSTACK if source == Source.PAYSTACK else SubscriptionPayment.Method.MPESA
        paid_on = min(timezone.localdate(paid_at), timezone.localdate())
        due = SubscriptionInvoice.objects.get(pk=invoice.pk).outstanding if invoice else amount
        try:
            with transaction.atomic():
                if invoice is not None:
                    payment = services.apply_payment(invoice, amount=amount, method=method, reference=code,
                                                     paid_on=paid_on, note=tx.get_source_display())
                else:
                    payment = services.apply_sms_top_up(top_up_org, amount=amount, method=method, reference=code,
                                                        paid_on=paid_on, note=tx.get_source_display()).payment
        except ValidationError as e:
            problems.append(" ".join(e.messages))
        else:
            tx.status, tx.payment = PlatformTransaction.Status.APPLIED, payment
            # A prompt rounds up to whole shillings; a shilling or more over is worth a look.
            if amount - due >= 1:
                tx.note = f"Paid {amount - due:,.2f} more than was due."
    if problems:
        tx.note = " ".join(problems)[:300]
    tx.save()
    if tx.status == PlatformTransaction.Status.UNMATCHED or tx.note:
        transaction.on_commit(lambda: _alert(tx))
    return tx, True


def _decimal(value) -> Decimal | None:
    return mpesa_c2b._amount(value)


def receive_stk(payload: dict) -> PlatformCheckout | None:
    """Safaricom's answer to a prompt we sent. Repeats and unknown requests change nothing."""
    cb = mpesa_stk._callback(payload)
    with transaction.atomic():
        checkout = (PlatformCheckout.objects.select_for_update(of=("self",)).select_related("invoice")
                    .filter(reference=str(cb["CheckoutRequestID"])[:80], provider=Provider.MPESA).first())
        if checkout is None:
            logger.warning("Subscription STK callback for unknown request %s", cb["CheckoutRequestID"])
            return None
        if checkout.status != PlatformCheckout.Status.PENDING:
            return checkout
        code, desc = str(cb.get("ResultCode", "")).strip(), str(cb.get("ResultDesc", "")).strip()
        if code != "0":
            _close(checkout, PlatformCheckout.Status.FAILED, desc or _("Not paid."))
            return checkout
        items = mpesa_stk._items(cb)
        receipt = _NOT_ALNUM.sub("", str(items.get("MpesaReceiptNumber") or "")).upper()[:20]
        if not receipt:
            # Paid, but nothing to record it by: the C2B confirmation carries it, matched by account number.
            checkout.result = "Paid; waiting for Safaricom's confirmation."
            checkout.save(update_fields=["result"])
            return checkout
        paid_at = mpesa_c2b._paid_at(items.get("TransactionDate")) or timezone.now()
        invoice = checkout.invoice
        tx, _created = _record(source=Source.STK, code=receipt, amount=_decimal(items.get("Amount")), paid_at=paid_at,
                               payload=payload, invoice=invoice, top_up_org=None if invoice else checkout.organization,
                               account=account_number(invoice) if invoice else SMS_ACCOUNT,
                               phone=str(items.get("PhoneNumber") or ""))
        _close(checkout, PlatformCheckout.Status.PAID, tx.note or desc)
    return checkout


def receive_c2b(payload: dict) -> PlatformTransaction:
    """A payment to our Paybill (typed by hand, or a prompt's own confirmation). Matched by account number."""
    code = _NOT_ALNUM.sub("", str(payload.get("TransID") or "")).upper()[:20]
    if not code:
        raise BadCallback("TransID missing")
    existing = PlatformTransaction.objects.filter(code=code).first()
    if existing is not None:
        return existing
    problems = []
    shortcode = str(payload.get("BusinessShortCode") or "").strip()
    if shortcode and shortcode != settings.PLATFORM_DARAJA_SHORTCODE:
        problems.append(f"Sent to shortcode {shortcode}, not ours ({settings.PLATFORM_DARAJA_SHORTCODE}).")
    account = str(payload.get("BillRefNumber") or "").strip()
    name = " ".join(str(payload.get(k) or "").strip() for k in ("FirstName", "MiddleName", "LastName")).split()
    with transaction.atomic():
        tx, _created = _record(
            source=Source.C2B, code=code, amount=_decimal(payload.get("TransAmount")),
            paid_at=mpesa_c2b._paid_at(payload.get("TransTime")) or timezone.now(), payload=payload,
            invoice=invoice_for_account(account), account=account, phone=str(payload.get("MSISDN") or ""),
            payer_name=" ".join(name), problems=problems)
    return tx


PAYSTACK_FAILED = ("failed", "abandoned", "reversed")


def receive_paystack(data: dict, payload: dict) -> PlatformTransaction | None:
    """A Paystack transaction, from the signed webhook or a verify call. Only our own checkouts count."""
    if not isinstance(data, dict):
        raise BadCallback("Paystack event without data")
    reference = str(data.get("reference") or "")[:80]
    state = str(data.get("status") or "").lower()
    with transaction.atomic():
        checkout = (PlatformCheckout.objects.select_for_update(of=("self",)).select_related("invoice")
                    .filter(reference=reference, provider=Provider.PAYSTACK).first())
        if checkout is None:
            logger.warning("Paystack event for unknown subscription checkout %s", reference)
            return None
        if state in PAYSTACK_FAILED:
            if checkout.status == PlatformCheckout.Status.PENDING:
                _close(checkout, PlatformCheckout.Status.FAILED,
                       str(data.get("gateway_response") or data.get("message") or _("Not paid.")))
            return None
        if state != "success":
            return None
        if not data.get("id"):
            raise BadCallback("Paystack transaction without id")
        try:
            amount = _decimal(Decimal(str(data.get("amount"))) / 100)
        except (InvalidOperation, ValueError):
            amount = None
        paid_at = dateparse.parse_datetime(str(data.get("paid_at") or data.get("paidAt") or "")) or timezone.now()
        customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
        name = " ".join(str(customer.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
        card = data.get("authorization") if isinstance(data.get("authorization"), dict) else {}
        if checkout.save_card and card:
            _save_card(checkout.organization, card)
        if card.get("authorization_code"):
            # The stored copy of the event never carries the code that can charge the card.
            payload = {**payload, "data": {**data, "authorization": {**card, "authorization_code": "***"}}}
        tx, _created = _record(
            source=Source.PAYSTACK, code=f"PS{data['id']}"[:30], amount=amount, paid_at=paid_at, payload=payload,
            invoice=checkout.invoice, top_up_org=None if checkout.invoice_id else checkout.organization,
            currency=str(data.get("currency") or "KES").upper()[:3], account=reference,
            phone=str(customer.get("phone") or ""), payer_name=name or str(customer.get("email") or ""))
        if checkout.status != PlatformCheckout.Status.PAID:
            # Paystack's success is final, even for a checkout the daily job had given up on.
            _close(checkout, PlatformCheckout.Status.PAID, tx.note or str(data.get("gateway_response") or ""))
    return tx


def verify_paystack(checkout: PlatformCheckout) -> PlatformCheckout:
    """Asks Paystack about a checkout, for when the landlord comes back before the webhook."""
    if checkout.status != PlatformCheckout.Status.PENDING:
        return checkout
    try:
        data = paystack.get_client(PlatformPaystack()).verify(checkout.reference)
    except paystack.PaystackError as e:
        logger.warning("Paystack verify for %s failed: %s", checkout.reference, e)
        return checkout
    receive_paystack(data, {"event": "verify", "data": data})
    checkout.refresh_from_db()
    return checkout


def _close(checkout: PlatformCheckout, status: str, result: str) -> None:
    checkout.status, checkout.result, checkout.completed_at = status, str(result)[:300], timezone.now()
    checkout.save(update_fields=["status", "result", "completed_at"])
    if checkout.automatic:
        transaction.on_commit(lambda: _tell_owner(checkout))


# ---------------------------------------------------------------------------
# Renewing by the saved card
# ---------------------------------------------------------------------------

MAX_CARD_ATTEMPTS = 4  # one a day; after that the normal overdue reminders and grace take over


def _save_card(org, card: dict) -> None:
    """Keeps the reusable card a successful payment came with, and turns automatic renewal on."""
    code = str(card.get("authorization_code") or "")
    if not code or not card.get("reusable") or str(card.get("channel") or "card") != "card":
        return
    sub = services.subscription_for(org)
    sub.card_code_encrypted = encrypt(code)
    sub.card_last4 = str(card.get("last4") or "")[-4:]
    sub.card_brand = str(card.get("brand") or card.get("card_type") or "").strip()[:30]
    sub.card_expiry = f"{card.get('exp_month') or ''}/{card.get('exp_year') or ''}".strip("/")[:7]
    sub.auto_renew = True
    sub.save(update_fields=["card_code_encrypted", "card_last4", "card_brand", "card_expiry", "auto_renew",
                            "updated_at"])
    audit.record("subscription.card_saved", organization=org, obj=sub, changes={"last4": sub.card_last4})


def _tell_owner(checkout: PlatformCheckout) -> None:
    """Emails the billing contact how a charge to the saved card went. Never fails the callback."""
    invoice, status = checkout.invoice, checkout.status
    if invoice is None or status == PlatformCheckout.Status.PENDING:
        return
    to = services.billing_contacts(checkout.organization)
    sub = services.subscription_for(checkout.organization)
    card = f"{sub.card_brand} ending {sub.card_last4}".strip()
    if status == PlatformCheckout.Status.PAID:
        subject = _("We renewed your NyumbaPMS plan")
        body = _("We charged KES %(a)s to your card (%(card)s) for invoice %(n)s. Your plan runs to %(d)s. The "
                 "receipt is on the Subscription page. To stop automatic renewal, turn it off there.") % {
            "a": f"{checkout.amount:,.2f}", "card": card, "n": invoice.number,
            "d": invoice.period_end or sub.period_end}
    else:
        left = max(MAX_CARD_ATTEMPTS - PlatformCheckout.objects.filter(invoice=invoice, automatic=True).count(), 0)
        subject = _("We could not charge your card for NyumbaPMS")
        body = _("We tried to charge KES %(a)s to your card (%(card)s) for invoice %(n)s and it did not go "
                 "through: %(why)s. %(again)s Pay on the Subscription page with a card or M-Pesa to keep your plan "
                 "going; paying by card there can also replace the saved card.") % {
            "a": f"{checkout.amount:,.2f}", "card": card, "n": invoice.number,
            "why": checkout.result or _("no reason given"),
            "again": _("We will try again tomorrow.") if left else _("We will not try again.")}
    if not to:
        return
    try:
        send_mail(f"[NyumbaPMS] {subject}", body, None, to, using=mailer_for(SYSTEM))
    except Exception:
        logger.exception("Card renewal email for invoice %s failed", invoice.number)


def charge_card(sub, invoice: SubscriptionInvoice) -> PlatformCheckout | None:
    """Charges the saved card for what is left on the invoice. The result comes back at once, and again by
    webhook; either way it is settled once. Returns the checkout, or None if the card cannot be used."""
    if not (sub.has_card and sub.auto_renew and methods()["paystack"]):
        return None
    org = sub.organization
    amount = invoice.outstanding
    if amount <= 0:
        return None
    checkout = PlatformCheckout.objects.create(
        organization=org, invoice=invoice, provider=Provider.PAYSTACK, reference=f"nps-{uuid.uuid4().hex}",
        amount=amount, automatic=True)
    email = invoice.customer_email or org.billing_email or f"billing-{org.pk}@{settings.PAYSTACK_EMAIL_DOMAIN}"
    try:
        data = paystack.get_client(PlatformPaystack()).charge_authorization(
            email=email, amount_cents=int(amount * 100), reference=checkout.reference,
            authorization_code=decrypt(sub.card_code_encrypted),
            metadata={"kind": "subscription", "invoice": invoice.number, "automatic": True})
    except paystack.PaystackError as e:
        logger.warning("Card renewal for %s refused: %s", invoice.number, e)
        with transaction.atomic():
            _close(checkout, PlatformCheckout.Status.FAILED, str(e))
        return checkout
    receive_paystack(data, {"event": "charge_authorization", "data": data})
    checkout.refresh_from_db()
    return checkout


def charge_due_cards(today: datetime.date | None = None) -> dict[str, int]:
    """Daily job: charges the saved card of each organization that turned automatic renewal on, once its
    renewal invoice is within `AUTO_CHARGE_DAYS_BEFORE` days of falling due (and every day after, up to
    MAX_CARD_ATTEMPTS, while it stays unpaid)."""
    from .models import Subscription

    today = today or timezone.localdate()
    counts = {"card_charged": 0, "card_failed": 0}
    subs = Subscription.objects.filter(auto_renew=True, status__in=[Subscription.Status.ACTIVE,
                                                                    Subscription.Status.PAST_DUE]
                                       ).exclude(card_code_encrypted="").select_related("organization")
    for sub in subs:
        invoice = services.open_invoice(sub)
        if (invoice is None or invoice.reason != SubscriptionInvoice.Reason.RENEWAL or invoice.disputed_at
                or today < invoice.due_on - datetime.timedelta(days=services.AUTO_CHARGE_DAYS_BEFORE)):
            continue
        tries = PlatformCheckout.objects.filter(invoice=invoice, automatic=True)
        if tries.count() >= MAX_CARD_ATTEMPTS or tries.filter(created_at__date=today).exists() or tries.filter(
                status=PlatformCheckout.Status.PENDING, created_at__gte=timezone.now() - GIVE_UP_AFTER).exists():
            continue
        try:
            checkout = charge_card(sub, invoice)
        except Exception:
            logger.exception("Card renewal for organization %s failed", sub.organization_id)
            continue
        if checkout is not None:
            counts["card_charged" if checkout.status == PlatformCheckout.Status.PAID else "card_failed"] += 1
    return counts


def give_up_waiting(now: datetime.datetime | None = None) -> int:
    """Closes checkouts nobody answered (daily job). A late success still settles them."""
    now = now or timezone.now()
    return PlatformCheckout.objects.filter(status=PlatformCheckout.Status.PENDING,
                                           created_at__lt=now - GIVE_UP_AFTER).update(
        status=PlatformCheckout.Status.FAILED, result="No answer.", completed_at=now)


# ---------------------------------------------------------------------------
# Setting up our Paybill (manage.py platform_payments)
# ---------------------------------------------------------------------------


def register_urls() -> dict:
    """Tells Safaricom to send payments made to our Paybill to our C2B callback."""
    if not mpesa_configured():
        raise ValidationError(_("Set PLATFORM_DARAJA_CONSUMER_KEY, _CONSUMER_SECRET, PLATFORM_DARAJA_SHORTCODE "
                                "(or PLATFORM_PAYBILL) and PLATFORM_CALLBACK_TOKEN first."))
    confirm, validate = check_callback_url("confirm"), check_callback_url("validate")
    try:
        return daraja.get_client(PlatformDaraja()).register_urls(confirm, validate)
    except daraja.DarajaError as e:
        raise ValidationError(_("Safaricom did not accept the addresses: %(error)s") % {"error": e}) from None
