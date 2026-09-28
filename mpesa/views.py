"""M-Pesa pages. Thin views; services do the work. Other organizations' records are a 404."""

import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can
from leases.models import Lease
from payments import selectors as payment_selectors
from payments.models import Payment, PaymentAccount

from . import forms, inbox, services
from .models import DarajaCredentials, MpesaTransaction

Status = MpesaTransaction.Status
PICKER_SIZE = 10


def _add_errors(form, exc: ValidationError) -> None:
    if hasattr(exc, "error_dict"):
        for field, errors in exc.error_dict.items():
            form.add_error(field if field in form.fields else None, errors)
    else:
        form.add_error(None, exc)


class SettingsView(CapabilityRequiredMixin, View):
    """The organization's Paybill and Till accounts and whether each is connected."""

    template_name = "mpesa/settings.html"
    required_capability = "mpesa.settings"

    def context(self, request, form=None):
        rows = []
        for account in services.mpesa_accounts(request.organization):
            creds = getattr(account, "daraja", None)
            rows.append({"account": account, "creds": creds})
        can_add = can(request.membership, "payment_accounts.manage")
        return {"rows": rows, "can_add": can_add, "form": form or (forms.AccountForm() if can_add else None)}

    def get(self, request):
        return render(request, self.template_name, self.context(request))

    def post(self, request):
        if not can(request.membership, "payment_accounts.manage"):
            raise Http404
        form = forms.AccountForm(request.POST)
        if form.is_valid():
            try:
                account = services.add_account(request.membership, **form.cleaned_data, request=request)
            except ValidationError as e:
                _add_errors(form, e)
            else:
                messages.success(request, _("Account added. Now enter its Daraja keys."))
                return redirect("mpesa:account", public_id=account.public_id)
        return render(request, self.template_name, self.context(request, form), status=400)


class AccountSettingsView(CapabilityRequiredMixin, View):
    """Daraja keys for one account, and registering its callback URLs with Safaricom."""

    template_name = "mpesa/account.html"
    required_capability = "mpesa.settings"

    def account(self, request, public_id) -> PaymentAccount:
        account = services.mpesa_accounts(request.organization).filter(public_id=public_id).first()
        if account is None:
            raise Http404
        return account

    def context(self, account, form=None):
        creds = DarajaCredentials.objects.filter(payment_account=account).first()
        initial = {"environment": creds.environment if creds else DarajaCredentials.Environment.SANDBOX,
                   "shortcode": creds.shortcode if creds and creds.shortcode != account.number else ""}
        form = form or forms.CredentialsForm(initial=initial)
        for name in DarajaCredentials.SECRETS:
            invalid = " is-invalid" if form.errors.get(name) else ""
            form.fields[name].widget.attrs.update({
                "class": "form-control" + invalid, "autocomplete": "new-password",
                "placeholder": _("Saved — leave blank to keep") if creds and creds.has(name) else _("Not set")})
        return {"account": account, "creds": creds, "form": form}

    def get(self, request, public_id):
        account = self.account(request, public_id)
        return render(request, self.template_name, self.context(account))

    def post(self, request, public_id):
        account = self.account(request, public_id)
        m = request.membership
        action = request.POST.get("action", "save")
        creds = DarajaCredentials.objects.filter(payment_account=account).first()
        if action in ("register", "new_token"):
            if creds is None:
                raise Http404
            try:
                if action == "register":
                    services.register_urls(m, creds, request=request)
                    messages.success(request, _("Safaricom will now send this account's payments here."))
                else:
                    services.new_token(m, creds, request=request)
                    messages.success(request, _("New callback link made. Register the addresses again."))
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))
            return redirect("mpesa:account", public_id=account.public_id)

        form = forms.CredentialsForm(request.POST)
        if form.is_valid():
            try:
                services.save_credentials(m, account, **form.cleaned_data, request=request)
            except ValidationError as e:
                _add_errors(form, e)
            else:
                messages.success(request, _("M-Pesa settings saved."))
                return redirect("mpesa:account", public_id=account.public_id)
        return render(request, self.template_name, self.context(account, form), status=400)


# ---------------------------------------------------------------------------
# Inbox and transactions (D-045 item 7)
# ---------------------------------------------------------------------------


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


def _act(request, tx: MpesaTransaction) -> str | None:
    """Runs one inbox action from a POST. Returns the error to show, or None on success."""
    m = request.membership
    action = request.POST.get("action", "")
    try:
        if action in ("accept", "match"):
            if action == "accept":
                payment = inbox.accept_suggestion(m, tx, request=request)
            else:
                lease_id = _uuid(request.POST.get("lease", ""))
                lease = Lease.objects.filter(organization=m.organization, public_id=lease_id).first()
                if lease is None:
                    raise Http404
                payment = inbox.match(m, tx, lease, request=request)
            messages.success(request, _("Matched to %(lease)s, confirmed and receipted.")
                             % {"lease": payment.lease.number})
        elif action == "ignore":
            inbox.ignore(m, tx, reason=request.POST.get("reason", ""), request=request)
            messages.success(request, _("Ignored. It stays in the transaction list."))
        elif action == "restore":
            inbox.restore(m, tx, request=request)
            messages.success(request, _("Put back in the inbox."))
        else:
            raise Http404
    except PermissionDenied:
        raise Http404 from None
    except ValidationError as e:
        return " ".join(e.messages)
    return None


class InboxView(CapabilityRequiredMixin, View):
    """Payments the matching engine could not place, oldest first."""

    template_name = "mpesa/inbox.html"
    required_capability = "mpesa.match"

    def get(self, request):
        items = inbox.inbox(request.membership)
        tab = Status.FLAGGED if request.GET.get("tab") == "flagged" else Status.UNMATCHED
        counts = {s: items.filter(status=s).count() for s in (Status.UNMATCHED, Status.FLAGGED)}
        rows = list(items.filter(status=tab)[:200])
        return render(request, self.template_name, {
            "rows": rows, "tab": tab, "unmatched": counts[Status.UNMATCHED], "flagged": counts[Status.FLAGGED],
            "more": counts[tab] > len(rows), "currency": request.organization.currency,
        })

    def post(self, request):
        tx = inbox.inbox(request.membership).filter(trans_id=request.POST.get("trans_id", "")).first()
        if tx is None:
            raise Http404
        error = _act(request, tx)
        if error:
            messages.error(request, error)
        return redirect("mpesa:inbox")


class TransactionListView(CapabilityRequiredMixin, View):
    """Every M-Pesa payment received, whatever happened to it."""

    template_name = "mpesa/transactions.html"
    required_capability = "mpesa.view_transactions"

    def get(self, request):
        m = request.membership
        qs = inbox.visible_transactions(m).order_by("-paid_at", "-pk")
        status = request.GET.get("status", "")
        if status in Status.values:
            qs = qs.filter(status=status)
        else:
            status = ""
        accounts = services.mpesa_accounts(m.organization)
        account = _uuid(request.GET.get("account", ""))
        if account:
            qs = qs.filter(payment_account__public_id=account)
        q = request.GET.get("q", "").strip()[:60]
        if q:
            qs = qs.filter(Q(trans_id__icontains=q) | Q(bill_ref__icontains=q) | Q(payer_name__icontains=q)
                           | Q(payer_phone__icontains=q))
        page = Paginator(qs, 50).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "page": page, "status": status, "statuses": Status.choices, "accounts": accounts,
            "account": str(account or ""), "q": q,
            "can_match": can(m, "mpesa.match"), "currency": m.organization.currency,
        })


class TransactionDetailView(CapabilityRequiredMixin, View):
    """One transaction: what M-Pesa sent, what happened, and matching it by hand."""

    template_name = "mpesa/transaction.html"
    required_capability = "mpesa.view_transactions"

    def transaction(self, request, trans_id) -> MpesaTransaction:
        tx = inbox.visible_transactions(request.membership).filter(trans_id=trans_id).first()
        if tx is None:
            raise Http404
        return tx

    def leases(self, m, tx, q):
        """Leases to match to: the search, else the typed reference, else the biggest balances."""
        found = list(payment_selectors.payable_leases(m, q=q or tx.bill_ref.strip())[:PICKER_SIZE + 1])
        if not found and not q:
            found = list(payment_selectors.payable_leases(m)[:PICKER_SIZE + 1])
        found = [lease for lease in found if lease.archived_at is None]
        return found[:PICKER_SIZE], len(found) > PICKER_SIZE

    def get(self, request, trans_id):
        m = request.membership
        tx = self.transaction(request, trans_id)
        can_match = inbox.visible_transactions(m, "mpesa.match").filter(pk=tx.pk).exists()
        q = request.GET.get("q", "").strip()[:60]
        leases, more = self.leases(m, tx, q) if can_match and tx.status == Status.UNMATCHED else ([], False)
        # Reversed ones too: a payment taken off a lease leaves its record under the same M-Pesa code.
        earlier = (Payment.objects.for_org(m.organization).filter(reference__iexact=tx.trans_id)
                   .exclude(pk=tx.payment_id or 0).select_related("lease__unit__property").order_by("-created_at"))
        return render(request, self.template_name, {
            "tx": tx, "can_match": can_match, "q": q, "leases": leases, "more": more, "earlier": earlier,
            "currency": m.organization.currency,
        })

    def post(self, request, trans_id):
        tx = self.transaction(request, trans_id)
        error = _act(request, tx)
        if error:
            messages.error(request, error)
        return redirect("mpesa:transaction", trans_id=tx.trans_id)
