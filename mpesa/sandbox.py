"""The open Daraja questions, asked of the sandbox in one sitting (D-104).

Each step goes through the app's own code (C2B receive, STK send, callback and query), so it tests the app as well
as Daraja, and returns a `Finding`: Safaricom's answer next to what the code assumes today. Nothing here changes an
assumption; the answers go into a later decision. Only sandbox credentials are accepted.
"""

import dataclasses
import hashlib
import time
from collections.abc import Callable
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from accounts.models import Membership
from core.phone import InvalidPhoneNumber, normalize_phone
from leases.models import Lease
from notifications import catalog
from notifications.rendering import render
from subscriptions.services import segments

from . import c2b, stk
from .daraja import DarajaError, get_client
from .models import DarajaCredentials, MpesaTransaction, StkRequest
from .services import callback_urls

# Safaricom's test payer for simulated C2B payments. [VERIFY on the Daraja portal's test credentials page]
TEST_MSISDN = "254708374149"
STEPS = ["token", "urls", "paybill", "till", "stk-query", "stk-c2b", "limit", "sms"]
NEEDS_PHONE = {"stk-query", "stk-c2b", "limit"}
NEEDS_KEYS = set(STEPS) - {"sms"}
RENT_MESSAGES = ["invoice_issued", "rent_due_soon", "rent_overdue"]
POLL_SECONDS = 3

# The GSM 03.38 alphabet: a text with any other character goes as UCS-2, 70 characters a part (67 when split).
_GSM = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
           "¿abcdefghijklmnopqrstuvwxyzäöñüà")
_GSM_EXT = set("^{}\\[~]|€")  # each counts as two


class SandboxOnly(Exception):
    pass


@dataclasses.dataclass
class Finding:
    step: str
    question: str
    answer: str
    assumption: str = ""
    verdict: str = "answered"  # matches, differs, answered, failed, skipped


@dataclasses.dataclass
class Context:
    creds: DarajaCredentials | None
    phone: str = ""
    reference: str = ""
    wait: int = 90
    site_url: str = ""
    sleep: Callable[[float], None] = time.sleep
    stk_request: StkRequest | None = None
    lease: Lease | None = None
    requested_by: object = None  # the user recorded as sending the test prompts; the owner when not given
    seen: set = dataclasses.field(default_factory=set)  # M-Pesa codes earlier steps took as their answer


def _requester(ctx: Context):
    """A test prompt is recorded as sent by the organization's owner, like one sent from the lease page."""
    if ctx.requested_by is None:
        seat = (Membership.objects.filter(organization_id=ctx.creds.organization_id, role__is_owner_role=True,
                                          is_active=True).select_related("user").order_by("created_at").first())
        ctx.requested_by = seat.user if seat else None
    return ctx.requested_by


def network_parts(text: str) -> int:
    """SMS parts as the network counts them: GSM-7 (160, then 153) or UCS-2 (70, then 67)."""
    if all(ch in _GSM or ch in _GSM_EXT for ch in text):
        n = sum(2 if ch in _GSM_EXT else 1 for ch in text)
        return 0 if not n else 1 if n <= 160 else -(-n // 153)
    n = len(text)
    return 0 if not n else 1 if n <= 70 else -(-n // 67)


def msisdn_form(raw: str, number: str = TEST_MSISDN) -> str:
    """How Daraja sent the payer's number: as the number, a hash of one of its forms, masked, or empty."""
    raw = (raw or "").strip()
    if not raw:
        return "empty"
    if c2b._HEX64.match(raw):
        local = "0" + number[3:]
        for label, form in (("2547XXXXXXXX", number), ("+2547XXXXXXXX", "+" + number), ("07XXXXXXXX", local)):
            if hashlib.sha256(form.encode()).hexdigest() == raw.lower():
                return f"SHA-256 of {label}"
        return "a 64-character hash of an unknown form"
    if "*" in raw or "X" in raw.upper():
        return f"masked ({raw})"
    try:
        normalize_phone(raw)
    except InvalidPhoneNumber:
        return f"something else ({raw[:20]})"
    return "the number itself"


def _wait_for(ctx: Context, check: Callable[[], object]) -> object:
    deadline = time.monotonic() + ctx.wait
    while True:
        found = check()
        if found or time.monotonic() >= deadline:
            return found
        ctx.sleep(POLL_SECONDS)


def _lease(ctx: Context) -> Lease | None:
    """An active lease this account collects for: the one with --reference, or the first."""
    if ctx.lease is not None:
        return ctx.lease
    leases = (Lease.objects.filter(organization_id=ctx.creds.organization_id, status=Lease.Status.ACTIVE)
              .select_related("unit__property").order_by("pk"))
    if ctx.reference:
        wanted = c2b.normalize_reference(ctx.reference)
        leases = [lease for lease in leases if c2b.normalize_reference(lease.unit.payment_reference) == wanted]
    for lease in leases:
        serves = any(c.payment_account_id == ctx.creds.payment_account_id for c in stk.stk_accounts(lease))
        if serves or not ctx.creds.can_request_payment:
            ctx.lease = lease
            return lease
    return None


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def step_token(ctx: Context) -> Finding:
    get_client(ctx.creds).token()
    return Finding("token", "Do the consumer key and secret work?", "Yes: Daraja gave an access token.",
                   verdict="matches")


def step_urls(ctx: Context) -> Finding:
    urls = callback_urls(ctx.creds)
    get_client(ctx.creds).register_urls(urls["confirm"], urls["validate"])
    ctx.creds.urls_registered_at, ctx.creds.registration_error = timezone.now(), ""
    ctx.creds.save(update_fields=["urls_registered_at", "registration_error", "updated_at"])
    return Finding("urls", "Does Daraja accept our C2B callback URLs?",
                   f"Yes, registered for {ctx.creds.shortcode} at {settings.SITE_URL}. Callbacks reach the app only "
                   "if that address is reachable from the internet (a tunnel in development).", verdict="matches")


def _simulate(ctx: Context, command: str, reference: str, amount: int) -> MpesaTransaction | None:
    """Each step simulates its own amount and skips codes an earlier step took, so a late or stray
    callback is not read as this step's answer."""
    started = timezone.now()
    get_client(ctx.creds).simulate_c2b(command=command, amount=amount, msisdn=TEST_MSISDN, bill_ref=reference)
    tx = _wait_for(ctx, lambda: MpesaTransaction.objects.filter(
        payment_account_id=ctx.creds.payment_account_id, source=MpesaTransaction.Source.C2B,
        created_at__gte=started, amount=amount).exclude(trans_id__in=ctx.seen).order_by("created_at").first())
    if tx is not None:
        ctx.seen.add(tx.trans_id)
    return tx


def step_paybill(ctx: Context) -> Finding:
    question = "In what form does a C2B confirmation carry the payer's phone, and does a reference match?"
    assumption = "MSISDN is the number or a SHA-256 of it as 2547XXXXXXXX (c2b.phone_hash, D-045 item 5)."
    lease = _lease(ctx)
    reference = lease.unit.payment_reference if lease else (ctx.reference or "TEST")
    tx = _simulate(ctx, "CustomerPayBillOnline", reference, amount=1)
    if tx is None:
        return Finding("paybill", question, f"No confirmation arrived within {ctx.wait} seconds. Check that the "
                       "URLs are registered and SITE_URL is reachable from the internet.", assumption, "failed")
    form = msisdn_form(tx.msisdn_raw)
    matched = ("matched to lease " + lease.number if tx.matched_by == MpesaTransaction.MatchedBy.REFERENCE
               else f"not matched by reference ({tx.get_status_display().lower()}: {tx.note or 'no note'})")
    verdict = "matches" if form in ("the number itself", "SHA-256 of 2547XXXXXXXX") else "differs"
    return Finding("paybill", question,
                   f"MSISDN came as {form}; account number {tx.bill_ref!r}, {matched}; code {tx.trans_id}.",
                   assumption, verdict)


def step_till(ctx: Context) -> Finding:
    question = "Does a Buy Goods (Till) confirmation carry an account number?"
    assumption = "BillRefNumber is empty for Tills, so they match by phone or by hand (D-100 item 4)."
    try:
        tx = _simulate(ctx, "CustomerBuyGoodsOnline", "TILLTEST", amount=2)
    except DarajaError as e:
        return Finding("till", question, f"Daraja refused a Buy Goods payment to {ctx.creds.shortcode}: {e}. "
                       "Try again with a Till sandbox shortcode.", assumption, "failed")
    if tx is None:
        return Finding("till", question, f"No confirmation arrived within {ctx.wait} seconds.", assumption, "failed")
    verdict = "matches" if not tx.bill_ref else "differs"
    return Finding("till", question, f"BillRefNumber was {tx.bill_ref!r} (sent 'TILLTEST'); type {tx.trans_type!r}.",
                   assumption, verdict)


def step_stk_query(ctx: Context) -> Finding:
    question = "What does a status query answer while the prompt is still on the phone?"
    assumption = f"An error with code {stk.STILL_PROCESSING} (stk.STILL_PROCESSING)."
    lease = _lease(ctx)
    if lease is None:
        return Finding("stk-query", question, "No active lease this Paybill collects for: add one, or pass "
                       "--reference.", assumption, "skipped")
    ctx.stk_request = stk.send(lease, phone=ctx.phone, amount=1, payment_account=ctx.creds.payment_account,
                               requested_by=_requester(ctx))
    try:
        data = get_client(ctx.creds).stk_query(ctx.stk_request.checkout_request_id)
    except DarajaError as e:
        verdict = "matches" if stk.STILL_PROCESSING in str(e) else "differs"
        return Finding("stk-query", question, f"An error: {e}.", assumption, verdict)
    return Finding("stk-query", question, f"A result, not an error: ResultCode {data.get('ResultCode')!r}, "
                   f"{data.get('ResultDesc', '')!r}.", assumption, "differs")


def step_stk_c2b(ctx: Context) -> Finding:
    question = "When a prompt is paid, does a C2B confirmation also arrive for the same receipt?"
    assumption = "Either may come, in either order; the receipt is stored once (stk.py, D-045 item 8)."
    request = ctx.stk_request
    if request is None:
        lease = _lease(ctx)
        if lease is None:
            return Finding("stk-c2b", question, "No active lease this Paybill collects for.", assumption, "skipped")
        request = ctx.stk_request = stk.send(lease, phone=ctx.phone, amount=1,
                                             payment_account=ctx.creds.payment_account,
                                             requested_by=_requester(ctx))
    paid = _wait_for(ctx, lambda: StkRequest.objects.filter(pk=request.pk, status=StkRequest.Status.PAID)
                     .select_related("transaction").first())
    if paid is None:
        request.refresh_from_db()
        return Finding("stk-c2b", question, f"The prompt to {ctx.phone} was not paid within {ctx.wait} seconds "
                       f"({request.get_status_display().lower()} {request.result_desc}). Approve it on the phone and "
                       "run this step again.", assumption, "failed")
    tx = paid.transaction
    if tx.source == MpesaTransaction.Source.C2B:
        return Finding("stk-c2b", question, f"Yes: the C2B confirmation for {tx.trans_id} came first, then the "
                       "payment request's callback.", assumption)
    tx = _wait_for(ctx, lambda: MpesaTransaction.objects.filter(pk=tx.pk, c2b_confirmed_at__isnull=False).first())
    if tx is None:
        return Finding("stk-c2b", question, f"No: only the payment request's callback came for "
                       f"{paid.transaction.trans_id} within {ctx.wait} seconds of it.", assumption)
    return Finding("stk-c2b", question, f"Yes: the callback for {tx.trans_id} came first, then a C2B confirmation.",
                   assumption)


def step_limit(ctx: Context) -> Finding:
    over = int(stk.MAX_AMOUNT) + 1
    question = f"Does Daraja refuse a prompt for KES {over:,}?"
    assumption = f"KES {stk.MAX_AMOUNT:,.0f} is the most per transaction (stk.MAX_AMOUNT)."
    url = callback_urls(ctx.creds)["stk"]
    try:
        data = get_client(ctx.creds).stk_push(phone=ctx.phone, amount=over, account_reference="LIMITTEST",
                                              description="Limit test", callback_url=url)
    except DarajaError as e:
        return Finding("limit", question, f"Refused when asked: {e}.", assumption, "matches")
    return Finding("limit", question, f"Accepted ({data.get('CheckoutRequestID')}); decline it on the phone. The "
                   "sandbox may not apply live limits, so confirm with Safaricom before raising the limit.",
                   assumption, "differs")


def step_sms(ctx: Context) -> Finding:
    """How long the rent messages get with the payment link, and what they cost. Needs no keys."""
    site = (ctx.site_url or settings.SITE_URL).rstrip("/")
    link = f"{site}/p/{'x' * 16}/"
    sample = {"tenant_name": "Wanjiku Kamau", "org_name": "Greenview Apartments", "unit": "A102",
              "property": "Greenview Court", "invoice_number": "INV-2026-000124", "amount": "KES 15,000.00",
              "amount_due": "KES 15,000.00", "balance": "KES 15,000.00", "due_date": "05/11/2026",
              "pay_reference": "GV-A102"}
    price = Decimal(str(getattr(settings, "SMS_PRICE", "1.00")))
    lines, worst, uncounted = [], 0, []
    for codename in RENT_MESSAGES:
        ntype = catalog.get(codename)
        for language in (catalog.EN, catalog.SW):
            body = ntype.bodies.get((catalog.SMS, language))
            if body is None:
                continue
            without = render(body, {**sample, "pay_link": ""})
            text = render(body, {**sample, "pay_link": link})
            billed, network = segments(text), network_parts(text)
            worst = max(worst, network)
            if network > billed:
                uncounted.append(f"{codename} ({language})")
            lines.append(f"{codename} ({language}): {len(without)} -> {len(text)} characters, "
                         f"{segments(without)} -> {network} part(s), KES {price * network:.2f}")
    answer = f"With a {len(link)}-character link ({site}): " + "; ".join(lines) + "."
    if uncounted:
        answer += f" The wallet charges fewer parts than the network counts for {', '.join(uncounted)}."
    return Finding("sms", "How long does a rent SMS get with the payment link, and what does it cost?", answer,
                   f"SMS_PRICE is KES {price} a part; at most {worst} part(s) a message here.", "answered")


STEP_FUNCTIONS = {"token": step_token, "urls": step_urls, "paybill": step_paybill, "till": step_till,
                  "stk-query": step_stk_query, "stk-c2b": step_stk_c2b, "limit": step_limit, "sms": step_sms}


def run(ctx: Context, steps: list[str]) -> list[Finding]:
    """Runs the steps in order. A step that fails is reported and the rest still run."""
    if ctx.creds is not None and ctx.creds.environment != DarajaCredentials.Environment.SANDBOX:
        raise SandboxOnly("This check sends test payments: it runs only on sandbox credentials.")
    findings = []
    for step in [s for s in STEPS if s in steps]:
        if step in NEEDS_KEYS and ctx.creds is None:
            findings.append(Finding(step, step, "Needs a sandbox account (--account).", verdict="skipped"))
            continue
        if step in NEEDS_PHONE and not ctx.phone:
            findings.append(Finding(step, step, "Needs --phone: a Safaricom number to send the prompt to.",
                                    verdict="skipped"))
            continue
        try:
            findings.append(STEP_FUNCTIONS[step](ctx))
        except Exception as e:  # noqa: BLE001 - one failing step must not hide the others' answers
            findings.append(Finding(step, step, f"Could not run: {e}", verdict="failed"))
    return findings


def markdown(findings: list[Finding], *, when=None) -> str:
    when = when or timezone.localtime()
    out = [f"# Daraja sandbox check, {when:%Y-%m-%d %H:%M}", "",
           "Written by `python manage.py daraja_sandbox_check` (D-104). Answers come from the sandbox, which may "
           "differ from live M-Pesa.", "", "| Step | Verdict | Question | Answer | What the code assumes |",
           "|---|---|---|---|---|"]
    for f in findings:
        cells = [f.step, f.verdict, f.question, f.answer, f.assumption]
        out.append("| " + " | ".join(c.replace("|", "/").replace("\n", " ") for c in cells) + " |")
    return "\n".join(out) + "\n"
