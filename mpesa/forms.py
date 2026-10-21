from django import forms
from django.utils.translation import gettext_lazy as _

from payments.models import PaymentAccount

from .models import DarajaCredentials


class AccountForm(forms.Form):
    type = forms.ChoiceField(label=_("Type"), choices=[(PaymentAccount.Type.PAYBILL, _("Paybill")),
                                                        (PaymentAccount.Type.TILL, _("Till (Buy Goods)"))],
                             help_text=_("A Paybill is best: tenants type their unit's reference as the account "
                                         "number, so each payment is matched at once, and you can send payment "
                                         "requests and pay links. Till payments have no account number, so each "
                                         "one waits in the inbox for you to match (D-100)."))
    number = forms.CharField(label=_("Number"), max_length=7)
    display_name = forms.CharField(label=_("Name"), max_length=80, required=False,
                                   help_text=_("e.g. Riverside rent Paybill"))


def _secret(label, help_text=""):
    # Never rendered back: the page says whether a value is stored.
    return forms.CharField(label=label, required=False, max_length=200, help_text=help_text,
                           widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}))


class CredentialsForm(forms.Form):
    environment = forms.ChoiceField(label=_("Keys from"), choices=[
        (DarajaCredentials.Environment.PRODUCTION, _("Live (real money): use this once Safaricom approved you")),
        (DarajaCredentials.Environment.SANDBOX, _("Sandbox (testing only, no real money)"))])
    shortcode = forms.CharField(label=_("Shortcode"), max_length=7, required=False,
                                help_text=_("Leave blank to use your Paybill number. A Till uses its store number."))
    consumer_key = _secret(_("Consumer key"), _("A long code on your app's page in Daraja."))
    consumer_secret = _secret(_("Consumer secret"), _("Next to the consumer key. Keep it private."))
    passkey = _secret(_("Passkey"), _("Safaricom sends it after Go-Live. Needed to request payments from a tenant's phone (Paybill)."))


class StkRequestForm(forms.Form):
    """A payment request to a tenant's phone. The service checks the phone and the amount."""

    phone = forms.CharField(label=_("Phone"), max_length=20,
                            help_text=_("The tenant gets a prompt to enter their M-Pesa PIN."))
    amount = forms.CharField(label=_("Amount (KES)"), max_length=12, help_text=_("Whole shillings."))
    account = forms.ChoiceField(label=_("Account"), required=False)

    def __init__(self, *args, accounts=(), phones=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].widget.attrs.update({"list": "stk-phones", "inputmode": "tel", "autocomplete": "off"})
        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})
        self.phones = list(phones)
        if len(accounts) > 1:
            self.fields["account"].choices = [(str(c.payment_account.public_id), c.payment_account.display_name)
                                              for c in accounts]
            self.fields["account"].required = True
        else:
            del self.fields["account"]


class PayLinkForm(forms.Form):
    """The tenant's side of the payment link. The service checks the phone, the amount and the limits."""

    phone = forms.CharField(label=_("M-Pesa phone number"), max_length=20,
                            help_text=_("You get a prompt on this phone to enter your M-Pesa PIN."))
    amount = forms.CharField(label=_("Amount (KES)"), max_length=12,
                             help_text=_("Any whole amount. You can pay in parts."))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["phone"].widget.attrs.update({"inputmode": "tel", "autocomplete": "tel",
                                                  "placeholder": "07XX XXX XXX"})
        self.fields["amount"].widget.attrs.update({"inputmode": "numeric"})
