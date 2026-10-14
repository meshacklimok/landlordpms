from django import forms
from django.utils.translation import gettext_lazy as _

from accounts.forms import PhoneField
from accounts.models import Organization
from core.models import EmailRouting

from . import access
from .models import Announcement, PlatformExpense, PlatformStaff


class EmailRoutingForm(forms.ModelForm):
    class Meta:
        model = EmailRouting
        fields = ["system_account", "messages_account"]


class SeatForm(forms.Form):
    role = forms.ChoiceField(label=_("Role"), choices=PlatformStaff.Role.choices)
    capabilities = forms.MultipleChoiceField(
        label=_("Pages they can open"), required=False, choices=list(access.CAPABILITIES.items()),
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Tick none to give the role's usual pages. Ticking some replaces them."))
    note = forms.CharField(label=_("Note"), max_length=200, required=False, help_text=_("What they do for us."))


class AddSeatForm(SeatForm):
    phone = PhoneField(help_text=_("The number they signed up with."))
    field_order = ["phone", "role", "capabilities", "note"]


class EditSeatForm(SeatForm):
    is_active = forms.BooleanField(label=_("Can sign in to the console"), required=False)


class OrganizationFilterForm(forms.Form):
    q = forms.CharField(required=False, label=_("Search"), max_length=80)
    status = forms.ChoiceField(required=False, label=_("Status"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["status"].choices = [("", _("Any status")), *Organization.Status.choices]


class OrganizationControlForm(forms.Form):
    action = forms.ChoiceField(choices=[("status", "status"), ("trial", "trial")], widget=forms.HiddenInput)
    status = forms.ChoiceField(label=_("Status"), required=False, choices=Organization.Status.choices)
    days = forms.IntegerField(label=_("Days to add"), required=False, min_value=1, max_value=90)
    reason = forms.CharField(label=_("Reason"), max_length=200, help_text=_("Kept in the audit trail."))


class AnnouncementForm(forms.Form):
    message = forms.CharField(label=_("Message"), max_length=300, widget=forms.Textarea(attrs={"rows": 2}))
    level = forms.ChoiceField(label=_("Kind"), choices=Announcement.Level.choices)
    ends_at = forms.DateTimeField(label=_("Ends (optional)"), required=False,
                                  widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
                                  input_formats=["%Y-%m-%dT%H:%M"])


class AuditFilterForm(forms.Form):
    action = forms.CharField(required=False, max_length=64)
    organization = forms.CharField(required=False, max_length=100)
    actor = forms.CharField(required=False, max_length=100)


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = PlatformExpense
        fields = ["spent_on", "category", "vendor", "amount", "repeat", "ends_on", "note"]
        widgets = {"spent_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "ends_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}
        help_texts = {"repeat": _("A subscription is entered once and repeats; it is counted in every month it applies to."),
                      "ends_on": _("Leave empty if it carries on.")}

    def clean(self):
        data = super().clean()
        if data.get("ends_on") and data.get("spent_on") and data["ends_on"] < data["spent_on"]:
            self.add_error("ends_on", _("This is before the date paid."))
        return data
