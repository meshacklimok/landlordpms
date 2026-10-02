from django import forms
from django.utils.translation import gettext_lazy as _

from accounts.forms import PhoneField

from .models import Tenant


class TenantForm(forms.ModelForm):
    phone = PhoneField()
    alt_phone = PhoneField(label=_("Other phone"), required=False)
    emergency_contact_phone = PhoneField(label=_("Emergency contact phone"), required=False)
    # Ticked after the page warns that the phone already belongs to another tenant.
    confirm_duplicate = forms.BooleanField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Tenant
        fields = ["kind", "name", "contact_person", "phone", "alt_phone", "email", "language",
                  "id_type", "id_number", "kra_pin",
                  "emergency_contact_name", "emergency_contact_phone", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}
        help_texts = {"contact_person": _("For a company tenant")}

    def __init__(self, *args, show_sensitive=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["id_type"].choices = [("", _("—"))] + list(Tenant.IdType.choices)
        self.fields["language"].required = False
        self.fields["language"].help_text = _("For SMS reminders and receipts")
        if not show_sensitive:
            for f in Tenant.SENSITIVE_FIELDS:
                del self.fields[f]

    def _post_clean(self):
        # Services validate and save; the form only parses input.
        pass

    def service_data(self) -> dict:
        data = dict(self.cleaned_data)
        data.pop("confirm_duplicate", None)
        data["language"] = data.get("language") or Tenant._meta.get_field("language").default
        return data


class TenantSearchForm(forms.Form):
    q = forms.CharField(required=False, max_length=100)
    status = forms.ChoiceField(required=False, choices=[("", _("All"))] + list(Tenant.Status.choices))
    archived = forms.BooleanField(required=False)
