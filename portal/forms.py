from django import forms
from django.utils.translation import gettext_lazy as _

from leases.forms import DateInput


class MessageForm(forms.Form):
    body = forms.CharField(label=_("Message"), max_length=1000, widget=forms.Textarea(attrs={"rows": 3}))


class OfferForm(forms.Form):
    """The office's renewal offer. Services validate; the form only parses input."""

    start_date = forms.DateField(label=_("Renewal starts"), widget=DateInput, required=False,
                                 help_text=_("Leave empty to start the day after this lease ends."))
    end_date = forms.DateField(label=_("Renewal ends"), widget=DateInput, required=False,
                               help_text=_("Leave empty for a month-to-month renewal."))
    rent = forms.DecimalField(label=_("Monthly rent (KES)"), max_digits=14, decimal_places=2, min_value=0)
    expires_on = forms.DateField(label=_("Tenant answers by"), widget=DateInput, required=False,
                                 help_text=_("Leave empty for 14 days from today."))
    note = forms.CharField(label=_("Note to the tenant"), max_length=500, required=False,
                           widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, initial_rent=None, **kwargs):
        super().__init__(*args, **kwargs)
        if initial_rent is not None and not self.is_bound:
            self.fields["rent"].initial = initial_rent


class OwnUseForm(forms.Form):
    """A tenant's note of their own electricity or water. own_use.add_entry checks the figures."""

    utility = forms.ChoiceField(label=_("Utility"), choices=[("ELECTRICITY", _("Electricity")), ("WATER", _("Water"))])
    kind = forms.ChoiceField(label=_("What are you adding?"),
                             choices=[("READING", _("Meter reading")), ("TOPUP", _("Tokens bought or bill paid"))])
    on = forms.DateField(label=_("Date"), widget=DateInput)
    value = forms.CharField(label=_("Meter shows, or units bought"), max_length=20, required=False)
    amount = forms.CharField(label=_("Amount paid (KES)"), max_length=14, required=False)
    note = forms.CharField(label=_("Note"), max_length=200, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs["class"] = "form-select form-select-sm" if name in ("utility", "kind") \
                else "form-control form-control-sm"
        self.fields["value"].widget.attrs["inputmode"] = "decimal"
        self.fields["amount"].widget.attrs["inputmode"] = "decimal"
