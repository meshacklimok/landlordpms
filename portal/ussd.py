"""The USSD menu for tenants on feature phones (D-081).

Africa's Talking posts every step of a session with the caller's number and everything typed so far,
joined by "*". The network vouches for the number, so no PIN is asked: the same trust as an SMS to it.
Nothing about the session is stored; each step is worked out again from the text.

Item 4 sends an M-Pesa prompt to the caller's own phone (D-084). A phone cannot show the prompt while the
session is open, so it is sent after the closing screen has gone out (`core.http.ReplyThen`).
The next item reports a repair for an active lease (D-085); items after 3 are numbered from what is offered.
Item 0 sets an optional PIN for the phone (D-086). Once set it is the first part of every step's text.
"""

import datetime
import hmac
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import Http404
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from accounts.models import Organization
from billing.invoicing import lease_balance
from billing.models import Invoice
from core import ratelimit
from core.http import ReplyThen
from core.money import ZERO, format_money
from core.phone import InvalidPhoneNumber, normalize_phone
from leases.models import Lease, LeaseTenant
from maintenance import services as maintenance
from mpesa import paylinks, stk
from mpesa.models import StkRequest
from payments.forms import accounts_for
from payments.models import Payment, PaymentAccount

from .models import UssdPin

logger = logging.getLogger(__name__)

MAX_CHARS = 182
MAX_LEASES = 5
MAX_ACCOUNTS = 2
STEPS_PER_HOUR = 30
LEASE_STATUSES = (Lease.Status.ACTIVE, Lease.Status.ENDED, Lease.Status.TERMINATED)
PROMPTS_PER_PHONE_DAY = 5
MAX_DESCRIPTION = 100
# "What is wrong?" options (D-085 item 2).
REPAIR_KINDS = ("PLUMBING", "ELECTRICAL", "DOORS", "STRUCTURE", "SECURITY", "OTHER")
PIN_TRIES = 5
PIN_LOCK = datetime.timedelta(hours=1)

# Swahili [VERIFY with a native speaker] (D-081 item 4).
TEXT = {
    "en": {
        "unknown": "This number is not registered for any lease. Ask your landlord to add it.",
        "limited": "Too many requests. Try again later.",
        "invalid": "Invalid choice.",
        "choose": "Choose your house:",
        "menu": "1. Balance\n2. How to pay\n3. Last payment",
        "owed": "Owed",
        "overdue": "Overdue",
        "nothing_owed": "Nothing owed.",
        "credit": "In credit",
        "next_due": "Next due",
        "on": "on",
        "no_accounts": "No payment account is set up. Ask your landlord.",
        "paybill": "Paybill",
        "till": "Till",
        "account": "Account",
        "no_payment": "No payment recorded yet.",
        "last_payment": "Last payment",
        "receipt": "Receipt",
        "menu_pay": "Pay with M-Pesa",
        "enter_amount": "Enter amount (KES):",
        "bad_amount": "Enter a whole amount from 1 to 250,000.",
        "confirm": "Pay {amount} for {house}?\n1. Confirm\n2. Cancel",
        "prompt_sent": "You will get an M-Pesa prompt on this phone. Enter your M-Pesa PIN to pay.",
        "cancelled": "Cancelled.",
        "wait": "A prompt was sent to this phone just now. Answer it, or try again in 2 minutes.",
        "too_many": "This phone has had as many M-Pesa prompts as it can today. Pay with the Paybill instead.",
        "menu_repair": "Report a repair",
        "repair_kind": "What is wrong?",
        "repair_kinds": ("Water or plumbing", "Electricity", "Doors and locks", "Roof and walls", "Security",
                         "Other"),
        "describe": "Describe it in a few words, or 0 to skip:",
        "emergency": "Is it an emergency?\n1. Yes\n2. No",
        "reported": "Thank you. Your repair number is {number}. We will send you updates by SMS.",
        "too_many_repairs": "You have reported 5 problems today. Call the office if there are more.",
        "menu_set_pin": "0. Set a PIN",
        "menu_pin": "0. PIN",
        "pin_menu": "PIN\n1. Change PIN\n2. Remove PIN",
        "enter_pin": "Enter your PIN:",
        "wrong_pin": "Wrong PIN.",
        "locked": "Too many wrong PINs. Try again in an hour, or ask your landlord to remove the PIN.",
        "new_pin": "Enter a new 4-digit PIN:",
        "again": "Enter the PIN again:",
        "weak_pin": "Choose 4 digits that are not all the same or in a row.",
        "mismatch": "The PINs did not match. Nothing changed.",
        "pin_set": "PIN set. You will be asked for it every time.",
        "pin_changed": "PIN changed.",
        "pin_removed": "PIN removed. The menu will not ask for it.",
    },
    "sw": {
        "unknown": "Nambari hii haijasajiliwa kwa upangaji wowote. Muulize mwenye nyumba aiongeze.",
        "limited": "Maombi mengi mno. Jaribu tena baadaye.",
        "invalid": "Chaguo si sahihi.",
        "choose": "Chagua nyumba yako:",
        "menu": "1. Salio\n2. Jinsi ya kulipa\n3. Malipo ya mwisho",
        "owed": "Deni",
        "overdue": "Imechelewa",
        "nothing_owed": "Hakuna deni.",
        "credit": "Umelipa zaidi",
        "next_due": "Malipo yajayo",
        "on": "tarehe",
        "no_accounts": "Hakuna akaunti ya malipo. Muulize mwenye nyumba.",
        "paybill": "Paybill",
        "till": "Till",
        "account": "Akaunti",
        "no_payment": "Hakuna malipo yaliyorekodiwa bado.",
        "last_payment": "Malipo ya mwisho",
        "receipt": "Risiti",
        "menu_pay": "Lipa kwa M-Pesa",
        "enter_amount": "Weka kiasi (KES):",
        "bad_amount": "Weka kiasi kamili kati ya 1 na 250,000.",
        "confirm": "Lipa {amount} kwa {house}?\n1. Thibitisha\n2. Ghairi",
        "prompt_sent": "Utapokea ombi la M-Pesa kwenye simu hii. Weka PIN yako ya M-Pesa ili kulipa.",
        "cancelled": "Imeghairiwa.",
        "wait": "Ombi limetumwa kwa simu hii sasa hivi. Lijibu, au jaribu tena baada ya dakika 2.",
        "too_many": "Simu hii imepokea maombi mengi ya M-Pesa leo. Lipa kupitia Paybill badala yake.",
        "menu_repair": "Ripoti tatizo la matengenezo",
        "repair_kind": "Tatizo ni nini?",
        "repair_kinds": ("Maji au mabomba", "Umeme", "Milango na kufuli", "Paa na kuta", "Usalama", "Mengine"),
        "describe": "Lieleze kwa maneno machache, au 0 kuruka:",
        "emergency": "Je, ni dharura?\n1. Ndiyo\n2. Hapana",
        "reported": "Asante. Nambari ya ombi lako ni {number}. Tutakutumia taarifa kwa SMS.",
        "too_many_repairs": "Umeripoti matatizo 5 leo. Piga simu ofisini kama kuna mengine.",
        "menu_set_pin": "0. Weka PIN",
        "menu_pin": "0. PIN",
        "pin_menu": "PIN\n1. Badilisha PIN\n2. Ondoa PIN",
        "enter_pin": "Weka PIN yako:",
        "wrong_pin": "PIN si sahihi.",
        "locked": "PIN zisizo sahihi mara nyingi. Jaribu tena baada ya saa moja, au muulize mwenye nyumba aiondoe.",
        "new_pin": "Weka PIN mpya ya tarakimu 4:",
        "again": "Weka PIN tena:",
        "weak_pin": "Chagua tarakimu 4 ambazo si sawa zote wala hazifuatani.",
        "mismatch": "PIN hazilingani. Hakuna kilichobadilika.",
        "pin_set": "PIN imewekwa. Utaulizwa kila mara.",
        "pin_changed": "PIN imebadilishwa.",
        "pin_removed": "PIN imeondolewa. Menyu haitaiuliza.",
    },
}


def _reply(kind: str, text: str) -> str:
    return f"{kind} {text}"[:MAX_CHARS]


def _normalized(phone: str) -> str:
    try:
        return normalize_phone(phone)
    except InvalidPhoneNumber:
        return ""


def caller_leases(phone: str) -> list[tuple[Lease, str, object]]:
    """The caller's leases with the tenant's language and the tenant, in a stable order (D-081 item 2).

    Main phone only, tenants not archived, organizations operating, leases issued, not archived and not renewed.
    """
    phone = _normalized(phone)
    if not phone:
        return []
    links = (LeaseTenant.objects
             .filter(tenant__phone=phone, tenant__archived_at__isnull=True,
                     tenant__organization__status=Organization.Status.ACTIVE,
                     tenant__organization__archived_at__isnull=True,
                     lease__archived_at__isnull=True, lease__status__in=LEASE_STATUSES)
             .select_related("tenant", "lease__unit__property", "lease__organization")
             .order_by("lease__organization__name", "lease__unit__property__name", "lease__unit__code", "lease__pk"))
    seen, result = set(), []
    for link in links:
        if link.lease_id in seen or link.lease.organization_id != link.tenant.organization_id:
            continue
        seen.add(link.lease_id)
        result.append((link.lease, link.tenant.language if link.tenant.language in TEXT else "en", link.tenant))
    return result[:MAX_LEASES]


def _label(lease: Lease) -> str:
    return f"{lease.unit.property.name} {lease.unit.code}"


def _date(day) -> str:
    return f"{day.day} {day:%b %Y}"


def balance_text(lease: Lease, t: dict) -> str:
    today = timezone.localdate()
    balance = lease_balance(lease)
    invoices = list(Invoice.objects.filter(lease=lease, status__in=Invoice.OPEN).order_by("due_date", "pk"))
    lines = [_label(lease)]
    if balance > 0:
        lines.append(f"{t['owed']}: {format_money(balance, lease.currency)}")
        overdue = sum((i.outstanding for i in invoices if i.overdue_after < today), ZERO)
        if overdue > 0:
            lines.append(f"{t['overdue']}: {format_money(overdue, lease.currency)}")
    else:
        lines.append(t["nothing_owed"])
        if balance < 0:
            lines.append(f"{t['credit']}: {format_money(-balance, lease.currency)}")
    upcoming = next((i for i in invoices if i.due_date >= today and i.outstanding > 0), None)
    if upcoming is not None:
        lines.append(f"{t['next_due']}: {format_money(upcoming.outstanding, lease.currency)} "
                     f"{t['on']} {_date(upcoming.due_date)}")
    return "\n".join(lines)


def pay_text(lease: Lease, t: dict) -> str:
    reference = lease.unit.payment_reference
    lines = []
    for account in accounts_for(lease).exclude(type=PaymentAccount.Type.CASH).exclude(number=""):
        if account.type == PaymentAccount.Type.PAYBILL:
            lines.append(f"{t['paybill']} {account.number}, {t['account']} {reference}")
        elif account.type == PaymentAccount.Type.TILL:
            lines.append(f"{t['till']} {account.number}")
        elif account.type == PaymentAccount.Type.BANK:
            lines.append(f"{account.display_name} {account.number}, {t['account']} {reference}")
        else:
            lines.append(f"M-Pesa {account.number}")
        if len(lines) == MAX_ACCOUNTS:
            break
    if not lines:
        return t["no_accounts"]
    return "\n".join([_label(lease), *lines])


def last_payment_text(lease: Lease, t: dict) -> str:
    payment = (Payment.objects.filter(lease=lease, status=Payment.Status.CONFIRMED).select_related("receipt")
               .order_by("-paid_at", "-pk").first())
    if payment is None:
        return t["no_payment"]
    lines = [_label(lease),
             f"{t['last_payment']}: {format_money(payment.amount, lease.currency)} {t['on']} {_date(payment.paid_at)}"]
    receipt = getattr(payment, "receipt", None)
    if receipt is not None:
        lines.append(f"{t['receipt']}: {receipt.number}")
    return "\n".join(lines)


SCREENS = {"1": balance_text, "2": pay_text, "3": last_payment_text}


def can_pay(lease: Lease, phone: str) -> bool:
    """Item 4 is offered when a payment link would work and the caller's number can get an M-Pesa prompt."""
    return phone.startswith("+254") and paylinks.can_take_payments(lease) and bool(stk.stk_accounts(lease))


def _pay(lease: Lease, phone: str, steps: list[str], t: dict):
    """Item 4: amount, confirm, then the prompt after the session ends (D-084 item 2)."""
    if len(steps) == 1:
        balance = lease_balance(lease)
        owed = f"{t['owed']}: {format_money(balance, lease.currency)}\n" if balance > 0 else ""
        return _reply("CON", f"{owed}{t['enter_amount']}"), None
    try:
        amount = stk._whole_shillings(steps[1])
    except ValidationError:
        return _reply("END", t["bad_amount"]), None
    if len(steps) == 2:
        house = _label(lease)
        return _reply("CON", t["confirm"].format(amount=format_money(amount, lease.currency), house=house)), None
    if len(steps) > 3 or steps[2] not in ("1", "2"):
        return _reply("END", t["invalid"]), None
    if steps[2] == "2":
        return _reply("END", t["cancelled"]), None
    now = timezone.now()
    if StkRequest.objects.filter(lease=lease, phone=phone, status=StkRequest.Status.PENDING,
                                 created_at__gte=now - stk.RESEND_AFTER).exists():
        return _reply("END", t["wait"]), None
    if StkRequest.objects.filter(via_ussd=True, phone=phone,
                                 created_at__gte=now - datetime.timedelta(days=1)).count() >= PROMPTS_PER_PHONE_DAY:
        return _reply("END", t["too_many"]), None

    def send_prompt():
        try:
            stk.send(lease, phone=phone, amount=amount, via_ussd=True)
        except ValidationError as exc:  # Safaricom refused; the request is kept as FAILED when it got that far
            logger.info("USSD M-Pesa prompt for lease %s not sent: %s", lease.pk, exc)

    return _reply("END", t["prompt_sent"]), send_prompt


def _repair(lease: Lease, tenant, steps: list[str], t: dict):
    """Report a repair: kind, a few words, emergency (D-085 item 2)."""
    if len(steps) == 1:
        kinds = "\n".join(f"{n}. {label}" for n, label in enumerate(t["repair_kinds"], 1))
        return _reply("CON", f"{t['repair_kind']}\n{kinds}")
    choice = steps[1]
    if not choice.isdigit() or not 1 <= int(choice) <= len(REPAIR_KINDS):
        return _reply("END", t["invalid"])
    if len(steps) == 2:
        return _reply("CON", t["describe"])
    description = "" if steps[2] == "0" else steps[2]
    if len(description) > MAX_DESCRIPTION:
        return _reply("END", t["invalid"])
    if len(steps) == 3:
        return _reply("CON", t["emergency"])
    if len(steps) > 4 or steps[3] not in ("1", "2"):
        return _reply("END", t["invalid"])
    try:
        req = maintenance.ussd_report(lease, tenant, kind=REPAIR_KINDS[int(choice) - 1], description=description,
                                      emergency=steps[3] == "1")
    except ValidationError:
        return _reply("END", t["too_many_repairs"])
    return _reply("END", t["reported"].format(number=req.number))


def pin_hash(phone: str, pin: str) -> str:
    return salted_hmac("portal.ussd.pin", f"{phone}:{pin}", algorithm="sha256").hexdigest()


def weak_pin(pin: str) -> bool:
    """Not 4 digits, all one digit, or a run up or down (D-086 item 1)."""
    return (len(pin) != 4 or not pin.isdigit() or len(set(pin)) == 1
            or pin in "0123456789" or pin in "9876543210")


def check_pin(record: UssdPin, entered: str, now) -> bool:
    """Counts a wrong PIN and locks after PIN_TRIES in a row; a right one resets the count (D-086 item 4)."""
    if constant_time_compare(pin_hash(record.phone, entered), record.pin_hash):
        if record.failures:
            UssdPin.objects.filter(pk=record.pk).update(failures=0)
        return True
    failures = record.failures + 1
    if failures >= PIN_TRIES:
        UssdPin.objects.filter(pk=record.pk).update(failures=0, locked_until=now + PIN_LOCK)
    else:
        UssdPin.objects.filter(pk=record.pk).update(failures=failures)
    record.failures = failures
    return False


def _save_pin(phone: str, pin: str) -> None:
    UssdPin.objects.update_or_create(phone=phone, defaults={"pin_hash": pin_hash(phone, pin), "failures": 0,
                                                            "locked_until": None})


def _new_pin(phone: str, entries: list[str], t: dict, done: str) -> str:
    """Two entries of a new PIN; `entries` is what was typed after reaching this screen."""
    if not entries:
        return _reply("CON", t["new_pin"])
    if weak_pin(entries[0]):
        return _reply("END", t["weak_pin"])
    if len(entries) == 1:
        return _reply("CON", t["again"])
    if len(entries) > 2:
        return _reply("END", t["invalid"])
    if entries[1] != entries[0]:
        return _reply("END", t["mismatch"])
    _save_pin(phone, entries[0])
    return _reply("END", t[done])


def _pin_item(phone: str, record: UssdPin | None, steps: list[str], t: dict) -> str:
    """Item 0: set a PIN, or change or remove it (D-086 items 1 and 5)."""
    if record is None:
        return _new_pin(phone, steps[1:], t, "pin_set")
    if len(steps) == 1:
        return _reply("CON", t["pin_menu"])
    if steps[1] == "1":
        return _new_pin(phone, steps[2:], t, "pin_changed")
    if steps[1] == "2" and len(steps) == 2:
        UssdPin.objects.filter(pk=record.pk).delete()
        return _reply("END", t["pin_removed"])
    return _reply("END", t["invalid"])


def extras(lease: Lease, phone: str) -> list[str]:
    """The items after 3, in order; numbered from 4 (D-085 item 1)."""
    items = []
    if can_pay(lease, phone):
        items.append("pay")
    if lease.status == Lease.Status.ACTIVE:
        items.append("repair")
    return items


def respond(phone: str, text: str) -> str:
    return handle(phone, text)[0]


def handle(phone: str, text: str):
    """The reply to one step ("CON ..." shows a menu, "END ..." finishes the session) and what to do once it
    has been sent, if anything."""
    leases = caller_leases(phone)
    if not leases:
        return _reply("END", TEXT["en"]["unknown"]), None
    languages = {language for _lease, language, _tenant in leases}
    list_language = languages.pop() if len(languages) == 1 else "en"
    number = _normalized(phone)
    if not ratelimit.hit(f"ussd:{number}", STEPS_PER_HOUR, 3600):
        return _reply("END", TEXT[list_language]["limited"]), None
    steps = [part.strip() for part in text.split("*")] if text else []
    record = UssdPin.objects.filter(phone=number).first()
    if record is not None:
        now = timezone.now()
        if record.locked_until and record.locked_until > now:
            return _reply("END", TEXT[list_language]["locked"]), None
        if not steps:
            return _reply("CON", TEXT[list_language]["enter_pin"]), None
        if not check_pin(record, steps.pop(0), now):
            key = "locked" if record.failures >= PIN_TRIES else "wrong_pin"
            return _reply("END", TEXT[list_language][key]), None
    if len(leases) > 1:
        if not steps:
            options = "\n".join(f"{n}. {_label(lease)}" for n, (lease, _language, _tenant) in enumerate(leases, 1))
            return _reply("CON", f"{TEXT[list_language]['choose']}\n{options}"), None
        choice = steps.pop(0)
        if not choice.isdigit() or not 1 <= int(choice) <= len(leases):
            return _reply("END", TEXT[list_language]["invalid"]), None
        lease, language, tenant = leases[int(choice) - 1]
    else:
        lease, language, tenant = leases[0]
    t = TEXT[language]
    items = extras(lease, number)
    if not steps:
        lines = [t["menu"], *(f"{n}. {t['menu_' + item]}" for n, item in enumerate(items, 4)),
                 t["menu_pin"] if record is not None else t["menu_set_pin"]]
        return _reply("CON", "\n".join([_label(lease), *lines])), None
    if steps[0] == "0":
        return _pin_item(number, record, steps, t), None
    item = items[int(steps[0]) - 4] if steps[0].isdigit() and 4 <= int(steps[0]) < 4 + len(items) else None
    if item == "pay":
        return _pay(lease, number, steps, t)
    if item == "repair":
        return _repair(lease, tenant, steps, t), None
    screen = SCREENS.get(steps[0]) if len(steps) == 1 else None
    if screen is None:
        return _reply("END", t["invalid"]), None
    return _reply("END", screen(lease, t)), None


@method_decorator(csrf_exempt, name="dispatch")
class UssdHookView(View):
    """Africa's Talking USSD callback: /hooks/ussd/africastalking/<token>/ (D-081 item 1)."""

    http_method_names = ["post"]

    def post(self, request, token):
        expected = getattr(settings, "AT_CALLBACK_TOKEN", "")
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            raise Http404
        reply, then = handle(request.POST.get("phoneNumber", ""), request.POST.get("text", ""))
        return ReplyThen(reply, content_type="text/plain; charset=utf-8", then=then)
