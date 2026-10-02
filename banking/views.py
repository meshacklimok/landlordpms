"""Bank pages: bank accounts, statement import, the bank inbox and bank lines (D-064). Thin views."""

import uuid

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can
from leases.models import Lease
from payments import selectors as payment_selectors

from . import inbox, services
from .models import BankTransaction, StatementImport

Status = BankTransaction.Status
PICKER_SIZE = 10


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


def _add_errors(form, exc: ValidationError) -> None:
    if hasattr(exc, "error_dict"):
        for name, errors in exc.message_dict.items():
            form.add_error(name if name in form.fields else None, errors)
    else:
        form.add_error(None, exc.messages)


# ---------------------------------------------------------------------------
# Bank accounts
# ---------------------------------------------------------------------------


class BankAccountForm(forms.Form):
    bank = forms.CharField(label=gettext_lazy("Bank"), max_length=60)
    number = forms.CharField(label=gettext_lazy("Account number"), max_length=40)
    properties = forms.ModelMultipleChoiceField(
        label=gettext_lazy("Collects rent for"), queryset=None, required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=gettext_lazy("Leave all unticked if it collects for every property."))

    def __init__(self, *args, properties=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["properties"].queryset = properties


class BankAccountsView(CapabilityRequiredMixin, View):
    template_name = "banking/accounts.html"
    required_capability = "payment_accounts.view"

    def context(self, request, form=None):
        m = request.membership
        can_manage = can(m, "payment_accounts.manage")
        if can_manage and form is None:
            form = BankAccountForm(properties=services.account_properties(m))
        return {"accounts": services.bank_accounts(m.organization), "form": form, "can_manage": can_manage}

    def get(self, request):
        return render(request, self.template_name, self.context(request))

    def post(self, request):
        m = request.membership
        if not can(m, "payment_accounts.manage"):
            raise PermissionDenied
        form = BankAccountForm(request.POST, properties=services.account_properties(m))
        if form.is_valid():
            try:
                account = services.add_bank_account(m, **form.cleaned_data, request=request)
            except ValidationError as e:
                _add_errors(form, e)
            else:
                messages.success(request, _("%(account)s added.") % {"account": account.display_name})
                return redirect("banking:accounts")
        return render(request, self.template_name, self.context(request, form), status=400)


# ---------------------------------------------------------------------------
# Statement import
# ---------------------------------------------------------------------------


class UploadForm(forms.Form):
    account = forms.ModelChoiceField(label=gettext_lazy("Account"), queryset=None, empty_label=None)
    file = forms.FileField(label=gettext_lazy("Statement (CSV)"))

    def __init__(self, *args, accounts=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = accounts
        self.fields["account"].label_from_instance = lambda a: f"{a.display_name} ({a.get_type_display()})"


class ImportView(CapabilityRequiredMixin, View):
    template_name = "banking/import.html"
    required_capability = "mpesa.match"

    def context(self, request, form=None):
        m = request.membership
        accounts = services.importable_accounts(m)
        return {"form": form or UploadForm(accounts=accounts), "has_accounts": accounts.exists(),
                "imports": services.visible_imports(m)[:20], "can_add_bank": can(m, "payment_accounts.manage")}

    def get(self, request):
        return render(request, self.template_name, self.context(request))

    def post(self, request):
        m = request.membership
        form = UploadForm(request.POST, request.FILES, accounts=services.importable_accounts(m))
        if form.is_valid():
            try:
                batch = services.preview(m, form.cleaned_data["account"], form.cleaned_data["file"], request=request)
            except ValidationError as e:
                form.add_error("file", e.messages)
            else:
                return redirect("banking:import_detail", public_id=batch.public_id)
        return render(request, self.template_name, self.context(request, form), status=400)


class ImportDetailView(CapabilityRequiredMixin, View):
    template_name = "banking/import_detail.html"
    required_capability = "mpesa.match"

    def batch(self, request, public_id) -> StatementImport:
        batch = services.visible_imports(request.membership).filter(public_id=public_id).first()
        if batch is None:
            raise Http404
        return batch

    def get(self, request, public_id):
        batch = self.batch(request, public_id)
        return render(request, self.template_name, {
            "batch": batch, "expired": services.is_expired(batch), "rows": batch.rows[:500],
            "currency": request.organization.currency,
            "inbox_url": "banking:inbox" if batch.kind == StatementImport.Kind.BANK else "mpesa:inbox",
        })

    def post(self, request, public_id):
        batch = self.batch(request, public_id)
        try:
            if request.POST.get("action") == "discard":
                services.discard(request.membership, batch)
                messages.success(request, _("Discarded. Nothing was imported."))
                return redirect("banking:import")
            batch = services.apply(request.membership, batch, request=request)
        except PermissionDenied:
            raise Http404 from None
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        else:
            messages.success(request, _("Imported %(n)s lines: %(m)s matched and receipted, %(u)s waiting in the "
                                        "inbox.") % {"n": batch.new_count, "m": batch.matched_count,
                                                      "u": batch.unmatched_count})
        return redirect("banking:import_detail", public_id=batch.public_id)


# ---------------------------------------------------------------------------
# Inbox and lines
# ---------------------------------------------------------------------------


def _act(request, line: BankTransaction) -> str | None:
    """Runs one inbox action from a POST. Returns the error to show, or None on success."""
    m = request.membership
    action = request.POST.get("action", "")
    try:
        if action == "accept":
            payment = inbox.accept_suggestion(m, line, request=request)
        elif action == "match":
            lease = Lease.objects.filter(organization=m.organization,
                                         public_id=_uuid(request.POST.get("lease", ""))).first()
            if lease is None:
                raise Http404
            payment = inbox.match(m, line, lease, request=request)
        elif action == "ignore":
            inbox.ignore(m, line, reason=request.POST.get("reason", ""), request=request)
            messages.success(request, _("Ignored. It stays in the list of bank lines."))
            return None
        elif action == "restore":
            inbox.restore(m, line, request=request)
            messages.success(request, _("Put back in the inbox."))
            return None
        else:
            raise Http404
    except PermissionDenied:
        raise Http404 from None
    except ValidationError as e:
        return " ".join(e.messages)
    messages.success(request, _("Matched to %(lease)s, confirmed and receipted.") % {"lease": payment.lease.number})
    return None


class InboxView(CapabilityRequiredMixin, View):
    template_name = "banking/inbox.html"
    required_capability = "mpesa.match"

    def get(self, request):
        items = inbox.inbox(request.membership)
        rows = list(items[:200])
        count = items.count()
        return render(request, self.template_name, {
            "rows": rows, "count": count, "more": count > len(rows), "currency": request.organization.currency,
        })

    def post(self, request):
        line = inbox.inbox(request.membership).filter(public_id=_uuid(request.POST.get("line", ""))).first()
        if line is None:
            raise Http404
        error = _act(request, line)
        if error:
            messages.error(request, error)
        return redirect("banking:inbox")


class LineListView(CapabilityRequiredMixin, View):
    template_name = "banking/lines.html"
    required_capability = "payments.view"

    def get(self, request):
        m = request.membership
        qs = inbox.visible_lines(m).order_by("-posted_on", "-pk")
        status = request.GET.get("status", "")
        if status in Status.values:
            qs = qs.filter(status=status)
        else:
            status = ""
        q = request.GET.get("q", "").strip()[:60]
        if q:
            qs = qs.filter(Q(description__icontains=q) | Q(reference__icontains=q))
        page = Paginator(qs, 50).get_page(request.GET.get("page"))
        return render(request, self.template_name, {
            "page": page, "status": status, "statuses": Status.choices, "q": q,
            "currency": m.organization.currency,
        })


class LineDetailView(CapabilityRequiredMixin, View):
    template_name = "banking/line.html"
    required_capability = "payments.view"

    def line(self, request, public_id) -> BankTransaction:
        line = inbox.visible_lines(request.membership).filter(public_id=public_id).first()
        if line is None:
            raise Http404
        return line

    def leases(self, m, q):
        found = list(payment_selectors.payable_leases(m, q=q)[:PICKER_SIZE + 1])
        found = [lease for lease in found if lease.archived_at is None]
        return found[:PICKER_SIZE], len(found) > PICKER_SIZE

    def get(self, request, public_id):
        m = request.membership
        line = self.line(request, public_id)
        can_match = inbox.visible_lines(m, "mpesa.match").filter(pk=line.pk).exists()
        q = request.GET.get("q", "").strip()[:60]
        leases, more = self.leases(m, q) if can_match and line.status == Status.UNMATCHED else ([], False)
        return render(request, self.template_name, {
            "line": line, "can_match": can_match, "q": q, "leases": leases, "more": more,
            "currency": m.organization.currency,
        })

    def post(self, request, public_id):
        line = self.line(request, public_id)
        error = _act(request, line)
        if error:
            messages.error(request, error)
        return redirect("banking:line", public_id=line.public_id)
