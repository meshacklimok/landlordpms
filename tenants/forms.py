from django import forms
from django.utils.translation import gettext_lazy as _

from accounts.forms import PhoneField

from .models import Tenant


def unit_label(u) -> str:
    """Property · block · unit code, the way a unit is named on tenant screens."""
    block = f" · {u.building.name}" if u.building_id else ""
    return f"{u.property.name}{block} · {u.code}"


class TenantForm(forms.ModelForm):
    phone = PhoneField()
    alt_phone = PhoneField(label=_("Other phone"), required=False)
    emergency_contact_phone = PhoneField(label=_("Emergency contact phone"), required=False)
    # Ticked after the page warns that the phone already belongs to another tenant.
    confirm_duplicate = forms.BooleanField(required=False, widget=forms.HiddenInput)
    # Add-tenant only: pick a vacant unit and carry straight on to its draft lease.
    unit = forms.ModelChoiceField(label=_("Assign to a unit"), queryset=None, required=False,
                                  help_text=_("Optional. Vacant units only. You set the rent and dates next."))

    class Meta:
        model = Tenant
        fields = ["kind", "name", "contact_person", "phone", "alt_phone", "email", "language",
                  "id_type", "id_number", "kra_pin", "student_number", "institution", "course", "year_of_study",
                  "emergency_contact_name", "emergency_contact_phone", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}
        help_texts = {"contact_person": _("For a company tenant")}

    STUDENT_FIELDS = ("student_number", "institution", "course", "year_of_study")

    def __init__(self, *args, show_sensitive=False, units=None, student=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.more_names = set(self.MORE_FIELDS)
        # Only a company has a contact person (ui.js reads show_when).
        self.fields["contact_person"].show_when = f"kind:{Tenant.Kind.COMPANY}"
        if student:
            # A hostel (D-188): who the student is and who pays for them, up front.
            self.fields["name"].label = _("Student name")
            self.fields["emergency_contact_name"].label = _("Parent or guardian")
            self.fields["emergency_contact_phone"].label = _("Parent or guardian phone")
            self.fields["emergency_contact_phone"].help_text = _(
                "They can pay fees from this phone: payments from it are matched to the student's booking.")
            self.fields["student_number"].help_text = _("The number on the student card or admission letter.")
            self.fields["unit"].label = _("Assign to a room or bed") if "unit" in self.fields else None
            self.more_names -= {"emergency_contact_name", "emergency_contact_phone"}
        else:
            for f in self.STUDENT_FIELDS:
                del self.fields[f]
        if units is None:
            del self.fields["unit"]
        else:
            self.fields["unit"].queryset = units
            self.fields["unit"].label_from_instance = unit_label
            self.order_fields(["kind", "name", "contact_person", "phone", "alt_phone", "email", "unit"])
        self.fields["id_type"].choices = [("", _("—"))] + list(Tenant.IdType.choices)
        self.fields["language"].required = False
        self.fields["language"].help_text = _("For SMS reminders and receipts")
        if not show_sensitive:
            for f in Tenant.SENSITIVE_FIELDS:
                del self.fields[f]

    # Not needed to start a tenancy; shown folded away so the first screen is short.
    MORE_FIELDS = ("alt_phone", "language", "id_type", "id_number", "kra_pin",
                   "emergency_contact_name", "emergency_contact_phone", "notes")

    def primary_fields(self):
        return [f for f in self if f.name not in self.more_names]

    def more_fields(self):
        return [f for f in self if f.name in self.more_names]

    def _post_clean(self):
        # Services validate and save; the form only parses input.
        pass

    def service_data(self) -> dict:
        data = dict(self.cleaned_data)
        data.pop("confirm_duplicate", None)
        data.pop("unit", None)
        data["language"] = data.get("language") or Tenant._meta.get_field("language").default
        return data


class TenantSearchForm(forms.Form):
    q = forms.CharField(required=False, max_length=100)
    status = forms.ChoiceField(required=False, choices=[("", _("All"))] + list(Tenant.Status.choices))
    property = forms.CharField(required=False, max_length=36)
    # "none": tenants with no active lease, so no unit yet.
    placed = forms.ChoiceField(required=False, choices=[("", _("All")), ("yes", _("In a unit")),
                                                           ("none", _("No unit yet"))])
    archived = forms.BooleanField(required=False)
    sort = forms.ChoiceField(required=False, choices=[("name", _("Name")), ("new", _("Newest first"))])
