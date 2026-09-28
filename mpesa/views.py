"""M-Pesa pages. Thin views; services do the work. Other organizations' records are a 404."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can
from payments.models import PaymentAccount

from . import forms, services
from .models import DarajaCredentials


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
