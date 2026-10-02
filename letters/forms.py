from django import forms
from django.utils.translation import gettext_lazy as _


class WithdrawForm(forms.Form):
    reason = forms.CharField(label=_("Why is it being withdrawn?"), max_length=300)


class SettingsForm(forms.Form):
    letter_show_payment_record = forms.BooleanField(
        label=_("On-time payment record"), required=False,
        help_text=_("How many invoices fell due, and how many were paid on time, late or not yet in full."))
    letter_show_balance = forms.BooleanField(
        label=_("Balance owed"), required=False, help_text=_("What the tenant owes, or is in credit, that day."))
    letter_show_deposit = forms.BooleanField(
        label=_("Deposit status"), required=False,
        help_text=_("Amount held, or once the tenancy ends, what was refunded and deducted. Never the reasons."))
    letter_show_rent = forms.BooleanField(label=_("Monthly rent"), required=False,
                                          help_text=_("The rent now, or at the end of the tenancy."))
