from django import forms
from django.utils.translation import gettext_lazy as _

from accounts.models import Organization


class TaxResidenceForm(forms.Form):
    landlord_tax_residence = forms.ChoiceField(
        label=_("Landlord tax residence"), choices=Organization.TaxResidence.choices, widget=forms.RadioSelect,
        help_text=_("Picks the rate in the tax estimate. Ask your tax adviser if you are not sure."))
