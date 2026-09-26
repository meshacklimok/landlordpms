from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Building, Property, Unit, clean_code


class CodeField(forms.CharField):
    """Uppercases and strips spaces before the code validators run."""

    def to_python(self, value):
        return clean_code(super().to_python(value))


class PropertyForm(forms.ModelForm):
    class Meta:
        model = Property
        fields = ["name", "code", "category", "county", "sub_county", "area", "street", "latitude", "longitude"]
        field_classes = {"code": CodeField}
        help_texts = {
            "code": _("Short letters or digits used in payment references, e.g. GV gives GV-A102. "
                      "It cannot be changed later."),
        }
        widgets = {
            "code": forms.TextInput(attrs={"style": "text-transform:uppercase", "autocapitalize": "characters"}),
            "latitude": forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"}),
            "longitude": forms.NumberInput(attrs={"step": "any", "inputmode": "decimal"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["county"].choices = [("", _("Choose county"))] + list(self.fields["county"].choices)[1:]
        if self.instance.pk:
            del self.fields["code"]
        else:
            self.fields["category"].help_text = _("A single house gets its unit (MAIN) automatically.")

    def _post_clean(self):
        # Services validate and save; skip ModelForm's own instance validation.
        pass


class BuildingForm(forms.Form):
    name = forms.CharField(label=_("Building name"), max_length=100)


class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ["code", "building", "unit_type", "type_label", "list_rent"]
        field_classes = {"code": CodeField}
        widgets = {
            "code": forms.TextInput(attrs={"style": "text-transform:uppercase", "autocapitalize": "characters"}),
            "list_rent": forms.NumberInput(attrs={"step": "0.01", "min": "0", "inputmode": "decimal"}),
        }

    def __init__(self, *args, property: Property, **kwargs):
        super().__init__(*args, **kwargs)
        buildings = Building.objects.filter(property=property)
        if buildings.exists():
            self.fields["building"].queryset = buildings
            self.fields["building"].empty_label = _("No building")
        else:
            del self.fields["building"]
        self.fields["list_rent"].help_text = _("Optional. The lease holds the actual rent.")

    def _post_clean(self):
        pass


class UnitStatusForm(forms.Form):
    manual_status = forms.ChoiceField(label=_("Status"), choices=Unit.ManualStatus.choices)


class PropertySearchForm(forms.Form):
    q = forms.CharField(required=False, max_length=100)
    archived = forms.BooleanField(required=False)


class UnitSearchForm(forms.Form):
    q = forms.CharField(required=False, max_length=100)
    property = forms.UUIDField(required=False)
    status = forms.CharField(required=False, max_length=20)
    unit_type = forms.ChoiceField(required=False, choices=[("", "")] + list(Unit.Type.choices))
