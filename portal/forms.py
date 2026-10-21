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
