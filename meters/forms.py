from django import forms
from django.utils.translation import gettext_lazy as _

from billing.forms import DateInput

from .models import Meter


class MeterForm(forms.Form):
    """The meter's own fields. The units it serves are posted as unit-<pk> and weight-<pk> boxes."""

    label = forms.CharField(label=_("Label"), max_length=60,
                            widget=forms.TextInput(attrs={"placeholder": _("e.g. A1 water")}))
    serial = forms.CharField(label=_("Serial number"), max_length=40, required=False)
    kind = forms.ChoiceField(label=_("Kind"), choices=Meter.Kind.choices, initial=Meter.Kind.POSTPAID)
    rate = forms.CharField(label=_("Price per m³ (KES)"), max_length=14, required=False)
    minimum_charge = forms.CharField(label=_("Minimum charge per unit (KES)"), max_length=14, required=False,
                                     help_text=_("Billed when the unit's share comes to less. Leave empty for none."))
    split = forms.ChoiceField(label=_("A shared meter is split"), choices=Meter.Split.choices,
                              initial=Meter.Split.EQUAL)


class ReadingForm(forms.Form):
    read_on = forms.DateField(label=_("Reading date"), widget=DateInput)
    value = forms.CharField(label=_("Reading (m³)"), max_length=20,
                            widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off"}))
    # A plain file field: inspections.photos checks and re-encodes it. Optional (D-057).
    photo = forms.FileField(label=_("Photo of the meter (optional)"), required=False,
                            widget=forms.ClearableFileInput(attrs={"accept": "image/*", "capture": "environment"}))
    note = forms.CharField(label=_("Note"), max_length=300, required=False)
    replaced = forms.BooleanField(label=_("A new meter was fitted: start counting from this reading"),
                                  required=False)


class RoundDateForm(forms.Form):
    read_on = forms.DateField(label=_("Reading date"), widget=DateInput)


class RejectForm(forms.Form):
    reason = forms.CharField(label=_("Why is it rejected?"), max_length=300)
