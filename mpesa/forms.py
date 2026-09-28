from django import forms
from django.utils.translation import gettext_lazy as _

from payments.models import PaymentAccount

from .models import DarajaCredentials


class AccountForm(forms.Form):
    type = forms.ChoiceField(label=_("Type"), choices=[(PaymentAccount.Type.PAYBILL, _("Paybill")),
                                                        (PaymentAccount.Type.TILL, _("Till (Buy Goods)"))])
    number = forms.CharField(label=_("Number"), max_length=7)
    display_name = forms.CharField(label=_("Name"), max_length=80, required=False,
                                   help_text=_("e.g. Riverside rent Paybill"))


def _secret(label, help_text=""):
    # Never rendered back: the page says whether a value is stored.
    return forms.CharField(label=label, required=False, max_length=200, help_text=help_text,
                           widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}))


class CredentialsForm(forms.Form):
    environment = forms.ChoiceField(label=_("Environment"), choices=DarajaCredentials.Environment.choices)
    shortcode = forms.CharField(label=_("Shortcode"), max_length=7, required=False,
                                help_text=_("Leave blank to use the account number. A Till uses its store number."))
    consumer_key = _secret(_("Consumer key"))
    consumer_secret = _secret(_("Consumer secret"))
    passkey = _secret(_("Passkey"), _("Only needed to request payments from a tenant's phone (Paybill)."))


class StkRequestForm(forms.Form):
    """A payment request to a tenant's phone. The service checks the phone and the amount."""

    phone = forms.CharField(label=_("Phone"), max_length=20,
                            help_text=_("The tenant gets a prompt to enter their M-Pesa PIN."))
    amount = forms.CharField(label=_("Amount (KES)"), max_length=12, help_text=_("Whole shillings."))
    account = forms.ChoiceField(label=_("Paybill"), required=False)

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
