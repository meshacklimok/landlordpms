from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from accounts import portfolio
from accounts.models import Branch
from accounts.permissions import can

from .models import Building, Property, PropertyOwner, Unit, clean_code


class CodeField(forms.CharField):
    """Uppercases and strips spaces before the code validators run."""

    def to_python(self, value):
        return clean_code(super().to_python(value))


class PropertyForm(forms.ModelForm):
    class Meta:
        model = Property
        fields = ["name", "code", "category", "county", "sub_county", "area", "street", "latitude", "longitude",
                  "service_days", "branch", "owner", "management_fee_percent", "management_fee_fixed",
                  "market_value"]
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

    def __init__(self, *args, organization=None, membership=None, **kwargs):
        super().__init__(*args, **kwargs)
        branches = Branch.objects.filter(organization=organization).order_by("name")
        if membership is None or not can(membership, "organization.manage") or not branches.exists():
            del self.fields["branch"]
        else:
            self.fields["branch"].queryset = branches
            self.fields["branch"].empty_label = _("No branch")
            self.fields["branch"].help_text = _("Staff given this branch see this property.")
        # A landlord with one property has no owner to report to and no fee to charge (D-150). The form for a
        # second property already offers them, since by then there is a portfolio.
        adding = not self.instance.pk
        if portfolio.agency_tools(organization) or (adding and portfolio.properties_so_far(organization)):
            self.fields["management_fee_fixed"].label = _("Fixed monthly fee (KES)")
            self.fields["management_fee_fixed"].widget.attrs.update({"step": "0.01", "inputmode": "decimal"})
            self.fields["owner"].queryset = PropertyOwner.objects.filter(organization=organization)
            self.fields["owner"].empty_label = _("Not set")
            self.fields["owner"].help_text = _("Used for the monthly owner statement. Add owners on the Owners page. "
                                               "For joint owners, use Owners and shares on the property page.")
        else:
            for name in ("owner", "management_fee_percent", "management_fee_fixed"):
                del self.fields[name]
        if organization is not None and organization.org_type in ("WATER", "WASTE"):
            self.fields["service_days"].label = organization.vocab["service_days"]
            del self.fields["market_value"]
        else:
            del self.fields["service_days"]
            self.fields["market_value"].label = _("Estimated value (%(c)s)") % {
                "c": organization.currency if organization is not None else "KES"}
            self.fields["market_value"].widget.attrs.update({"step": "1", "inputmode": "decimal"})
        self.fields["county"].choices = [("", _("Choose county"))] + list(self.fields["county"].choices)[1:]
        if self.instance.pk:
            del self.fields["code"]
        else:
            self.fields["category"].help_text = _("A single house gets its unit (MAIN) automatically.")

    def _post_clean(self):
        # Services validate and save; skip ModelForm's own instance validation.
        pass


class ShareForm(forms.Form):
    """One owner and their share of the property (D-078). A row with no owner is ignored."""

    owner = forms.ModelChoiceField(label=_("Owner"), queryset=PropertyOwner.objects.none(), required=False,
                                   empty_label=_("Choose owner"))
    percent = forms.DecimalField(label=_("Share (%)"), max_digits=5, decimal_places=2, min_value=Decimal("0.01"),
                                 max_value=100, required=False,
                                 widget=forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"}))

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"].queryset = PropertyOwner.objects.filter(organization=organization)

    def clean(self):
        data = super().clean()
        if data.get("owner") and data.get("percent") is None:
            self.add_error("percent", _("Enter the share."))
        return data


ShareFormSet = forms.formset_factory(ShareForm, extra=0, max_num=10, validate_max=True)


def share_formset(organization, data=None, initial=None):
    initial = list(initial or [])
    return ShareFormSet(data, initial=initial + [{}] * max(2, 4 - len(initial)) if data is None else None,
                        form_kwargs={"organization": organization}, prefix="shares")


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
    branch = forms.UUIDField(required=False)
    archived = forms.BooleanField(required=False)


class UnitSearchForm(forms.Form):
    q = forms.CharField(required=False, max_length=100)
    property = forms.UUIDField(required=False)
    status = forms.CharField(required=False, max_length=20)
    unit_type = forms.ChoiceField(required=False, choices=[("", "")] + list(Unit.Type.choices))


class PropertyOwnerForm(forms.ModelForm):
    class Meta:
        model = PropertyOwner
        fields = ["name", "phone", "email", "note"]

    def _post_clean(self):
        pass
