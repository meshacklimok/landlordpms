from django import forms
from django.utils.translation import gettext_lazy as _

from accounts.models import Organization


class TaxResidenceForm(forms.Form):
    landlord_tax_residence = forms.ChoiceField(
        label=_("Landlord tax residence"), choices=Organization.TaxResidence.choices, widget=forms.RadioSelect,
        help_text=_("Picks the rate in the tax estimate. Ask your tax adviser if you are not sure."))


class DashboardFilterForm(forms.Form):
    """The dashboard's filters, kept in the URL. A bad value is dropped rather than shown as an error."""

    month = forms.DateField(required=False, input_formats=["%Y-%m"],
                            widget=forms.DateInput(attrs={"type": "month", "class": "form-control form-control-sm",
                                                       "aria-label": _("Month")}, format="%Y-%m"))
    property = forms.ModelChoiceField(queryset=None, required=False, to_field_name="public_id",
                                      empty_label=_("All properties"))

    def __init__(self, data, *, properties):
        super().__init__(data)
        self.fields["property"].queryset = properties
        self.fields["property"].widget.attrs.update({"class": "form-select form-select-sm",
                                                     "aria-label": _("Property")})

    def value(self, name):
        self.is_valid()
        return None if name in self.errors else self.cleaned_data.get(name)


class OwnerStatementForm(forms.Form):
    owner = forms.ChoiceField(required=False)
    month = forms.DateField(required=False, input_formats=["%Y-%m"],
                            widget=forms.DateInput(attrs={"type": "month", "class": "form-control form-control-sm",
                                                       "aria-label": _("Month")}, format="%Y-%m"))

    def __init__(self, data, *, owners):
        super().__init__(data)
        self.fields["owner"].choices = owners
        self.fields["owner"].widget.attrs.update({"class": "form-select form-select-sm", "aria-label": _("Owner")})

    def value(self, name):
        if not self.is_bound:
            return None
        self.is_valid()
        return None if name in self.errors else self.cleaned_data.get(name)
