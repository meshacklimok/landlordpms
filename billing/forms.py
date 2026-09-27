import datetime

from django import forms
from django.utils.translation import gettext_lazy as _

from .models import ChargeType, DepositEntry


class ChargeTypeForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=60)
    category = forms.ChoiceField(
        label=_("Category"),
        choices=[c for c in ChargeType.Category.choices if c[0] not in ChargeType.NOT_RECURRING],
        initial=ChargeType.Category.OTHER,
    )


class DateInput(forms.DateInput):
    input_type = "date"


class MonthField(forms.CharField):
    """A month picker; cleans to the first day of the month."""

    widget = forms.TextInput(attrs={"type": "month"})

    def to_python(self, value):
        value = super().to_python(value)
        if not value:
            return None
        try:
            return datetime.datetime.strptime(value, "%Y-%m").date()
        except ValueError:
            raise forms.ValidationError(_("Choose a month.")) from None


class GenerateForm(forms.Form):
    month = MonthField(label=_("Month to bill"))


class VoidForm(forms.Form):
    reason = forms.CharField(label=_("Why is it being voided?"), max_length=300)


class OpeningBalanceForm(forms.Form):
    amount = forms.CharField(label=_("Balance brought forward"), max_length=20,
                             help_text=_("What the tenant owed on that day. Negative if they had paid ahead; "
                                         "0 clears it."))
    as_of = forms.DateField(label=_("As of"), widget=DateInput)
    reason = forms.CharField(label=_("Note"), max_length=300, required=False)


class StatementForm(forms.Form):
    start = forms.DateField(label=_("From"), required=False, widget=DateInput)
    end = forms.DateField(label=_("To"), required=False, widget=DateInput)


class DepositForm(forms.Form):
    """Receive, deduct or refund; the view says which."""

    amount = forms.CharField(label=_("Amount"), max_length=20)
    deposit_type = forms.ChoiceField(label=_("Deposit"), choices=DepositEntry.DepositType.choices)
    entry_date = forms.DateField(label=_("Date"), widget=DateInput)
    reference = forms.CharField(label=_("Reference"), max_length=60, required=False,
                                help_text=_("M-Pesa code, cheque or bank reference."))
    reason = forms.CharField(label=_("Reason"), max_length=300, required=False)


class DeductForm(DepositForm):
    reference = None
    apply_to_balance = forms.BooleanField(label=_("Put it towards what the tenant owes"), required=False)


class ReverseDepositForm(forms.Form):
    reason = forms.CharField(label=_("Why is it being corrected?"), max_length=300)
