from django import forms
from django.utils.translation import gettext_lazy as _

from billing.forms import DateInput

from .models import Meter


class MeterForm(forms.Form):
    """The meter's own fields. The units it serves are posted as unit-<pk> and weight-<pk> boxes."""

    utility = forms.ChoiceField(label=_("Meter for"), choices=Meter.Utility.choices, initial=Meter.Utility.WATER)
    label = forms.CharField(label=_("Label"), max_length=60,
                            widget=forms.TextInput(attrs={"placeholder": _("e.g. A1 water")}))
    serial = forms.CharField(label=_("Serial number"), max_length=40, required=False)
    kind = forms.ChoiceField(label=_("Kind"), choices=Meter.Kind.choices, initial=Meter.Kind.POSTPAID)
    rate = forms.CharField(label=_("Price per m³ or per kWh (KES)"), max_length=14, required=False,
                           help_text=_("For electricity, charge what the rules allow; the app does not check it."))
    minimum_charge = forms.CharField(label=_("Minimum charge per unit (KES)"), max_length=14, required=False,
                                     help_text=_("Billed when the unit's share comes to less. Leave empty for none."))
    payer = forms.ChoiceField(label=_("Who pays"), choices=Meter.Payer.choices, initial=Meter.Payer.TENANTS,
                              required=False,
                              help_text=_("Choose \"Landlord pays\" when water or power is free to the tenants, e.g. "
                                          "included in the rent. Readings are kept to follow the use but nothing "
                                          "is billed, and the meter is not waited for when you bill a month. Also "
                                          "use it for rooms' own meters that only decide how a shared meter is "
                                          "divided."))
    split = forms.ChoiceField(label=_("A shared meter is divided"), choices=Meter.Split.choices,
                              initial=Meter.Split.EQUAL,
                              help_text=_("By weight uses the weights below, e.g. bedrooms or people. By each room's "
                                          "own meter needs a reading of each room's meter on the same dates. You can "
                                          "compare the ways and pick another when approving a reading."))
    vacant_share = forms.ChoiceField(label=_("Empty rooms on a shared meter"), choices=Meter.VacantShare.choices,
                                     initial=Meter.VacantShare.OCCUPIED, required=False)

    def clean_payer(self):
        return self.cleaned_data.get("payer") or Meter.Payer.TENANTS

    def clean_vacant_share(self):
        return self.cleaned_data.get("vacant_share") or Meter.VacantShare.OCCUPIED


class ReadingForm(forms.Form):
    read_on = forms.DateField(label=_("Reading date"), widget=DateInput)
    value = forms.CharField(label=_("Reading"), max_length=20,
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
