from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from accounts import portfolio
from accounts.models import Branch
from accounts.permissions import can

from .models import Building, Property, PropertyOwner, Room, Unit, clean_code


class CodeField(forms.CharField):
    """Uppercases and strips spaces before the code validators run."""

    def to_python(self, value):
        return clean_code(super().to_python(value))


class PropertyForm(forms.ModelForm):
    class Meta:
        model = Property
        # Sub-county, street and map coordinates are no longer asked for (old values still show where set).
        fields = ["name", "code", "category", "county", "area",
                  "service_days", "branch", "owner", "management_fee_percent", "management_fee_fixed",
                  "market_value"]
        field_classes = {"code": CodeField}
        labels = {"area": _("Town or place")}
        help_texts = {
            "name": _("The name your tenants know it by, e.g. Greenview Apartments."),
            "area": _("e.g. Kilimani, Ruiru, Nakuru town."),
            "code": _("Short letters or digits used in payment references, e.g. GV gives GV-A102. "
                      "It cannot be changed later."),
        }
        widgets = {
            "code": forms.TextInput(attrs={"style": "text-transform:uppercase", "autocapitalize": "characters"}),
            "area": forms.TextInput(attrs={"autocomplete": "address-level2", "placeholder": _("Town or place")}),
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
            # The fee is taken on the owner's statement, so its boxes show once an owner is chosen (ui.js).
            for name in ("management_fee_percent", "management_fee_fixed"):
                self.fields[name].show_when = "owner:*"
        else:
            for name in ("owner", "management_fee_percent", "management_fee_fixed"):
                del self.fields[name]
        if organization is not None and organization.org_type in ("WATER", "WASTE"):
            self.fields["service_days"].label = organization.vocab["service_days"]
            self.fields["name"].help_text = _("The name your %(tenants)s know it by.") % {
                "tenants": str(organization.vocab["tenants"]).lower()}
            del self.fields["market_value"]
            del self.fields["category"]  # building types mean nothing for a route or a zone
        else:
            del self.fields["service_days"]
            if organization is not None and organization.org_type == "HOSTEL" and adding:
                # A hostel is always a hostel (D-188): no building type to choose.
                self.fields["category"].initial = Property.Category.HOSTEL
                self.fields["category"].widget = forms.HiddenInput()
                self.fields["name"].help_text = _("The name students know it by, e.g. Campus View Hostel.")
            self.fields["market_value"].label = _("Estimated value (%(c)s)") % {
                "c": organization.currency if organization is not None else "KES"}
            self.fields["market_value"].widget.attrs.update({"step": "1", "inputmode": "decimal"})
        self.fields["county"].choices = [("", _("Choose county"))] + list(self.fields["county"].choices)[1:]
        # Headings that partials/form.html prints above the first field of each group.
        for name, heading in (("name", _("Basics")), ("county", _("Location")), ("service_days", _("Service")),
                              ("market_value", _("Value")), ("owner", _("Owner and fee")), ("branch", _("Branch"))):
            if name in self.fields:
                self.fields[name].heading = heading
        if self.instance.pk:
            del self.fields["code"]
        elif "category" in self.fields:
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


class RoomsForm(forms.Form):
    """Add rooms to a hostel: a run of numbers such as R101 to R112, each with beds let one by one or let whole."""

    LET_BEDS, LET_WHOLE = "beds", "whole"

    prefix = forms.CharField(label=_("Room number starts with"), max_length=4, required=False,
                             help_text=_("Letters before the number, e.g. R or A. Can be empty."))
    first = forms.IntegerField(label=_("First room number"), min_value=0, max_value=9999, initial=101)
    count = forms.IntegerField(label=_("How many rooms"), min_value=1, max_value=300, initial=1,
                               help_text=_("Rooms are numbered one after another: 101, 102, 103…"))
    let = forms.ChoiceField(label=_("How the room is let"), widget=forms.RadioSelect, initial=LET_BEDS,
                            choices=[(LET_BEDS, _("Bed by bed: each bed has its own student, fee and payment reference")),
                                     (LET_WHOLE, _("Whole room: one booking for the room, even with several students"))])
    capacity = forms.IntegerField(label=_("Beds in each room"), min_value=1, max_value=50, initial=4,
                                  help_text=_("For a room let whole: how many students it holds."))
    price = forms.DecimalField(label=_("Fee per month (KES)"), max_digits=14, decimal_places=2, min_value=0,
                               required=False,
                               help_text=_("Per bed, or for the whole room when let whole. A semester is four months of it. "
                                           "Optional: you set the real fee on each booking."))
    gender = forms.ChoiceField(label=_("Who the rooms are for"), choices=Room.Gender.choices, required=False)
    building = forms.ModelChoiceField(label=_("Block or floor"), queryset=None, required=False)

    def __init__(self, *args, property: Property, **kwargs):
        super().__init__(*args, **kwargs)
        buildings = Building.objects.filter(property=property)
        if buildings.exists():
            self.fields["building"].queryset = buildings
            self.fields["building"].empty_label = _("No block")
        else:
            del self.fields["building"]
        self.fields["gender"].choices = [("", _("Anyone"))] + [c for c in Room.Gender.choices if c[0]]
        self.fields["price"].widget.attrs.update({"step": "0.01", "inputmode": "decimal"})

    def clean(self):
        data = super().clean()
        if data.get("let") == self.LET_BEDS and (data.get("capacity") or 0) > 26:
            self.add_error("capacity", _("A room with beds let one by one holds up to 26 beds."))
        return data


class RoomEditForm(forms.Form):
    gender = forms.ChoiceField(label=_("Who the room is for"), required=False)
    capacity = forms.IntegerField(label=_("Beds"), min_value=1, max_value=50,
                                  help_text=_("You can add beds here. To take a bed out, archive it once its lease has ended."))

    def __init__(self, *args, room, **kwargs):
        kwargs.setdefault("initial", {"gender": room.gender, "capacity": room.capacity})
        super().__init__(*args, **kwargs)
        self.fields["gender"].choices = [("", _("Anyone"))] + [c for c in Room.Gender.choices if c[0]]
        if room.let_whole:
            self.fields["capacity"].label = _("Students it holds")


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
    archived = forms.BooleanField(required=False)


class BulkUnitForm(forms.Form):
    """Rent and status changes for the units ticked on the unit list."""

    rent = forms.DecimalField(required=False, min_value=0, max_digits=12, decimal_places=2)
    percent = forms.DecimalField(required=False, min_value=-90, max_value=500, max_digits=6, decimal_places=2)
    status = forms.ChoiceField(required=False, choices=[("", "")] + list(Unit.ManualStatus.choices))

    def clean(self):
        data = super().clean()
        if data.get("rent") is not None and data.get("percent") is not None:
            raise forms.ValidationError(_("Choose a new rent or a percentage change, not both."))
        if data.get("rent") is None and data.get("percent") is None and not data.get("status"):
            raise forms.ValidationError(_("Choose a new rent, a percentage change or a status."))
        return data


class PropertyOwnerForm(forms.ModelForm):
    class Meta:
        model = PropertyOwner
        fields = ["name", "phone", "email", "note"]

    def _post_clean(self):
        pass
