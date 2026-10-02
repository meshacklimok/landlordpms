from django import forms
from django.utils.translation import gettext_lazy as _

from expenses.forms import ExpenseForm

from .models import MaintenanceRequest


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class PhotosField(forms.FileField):
    """Several photos in one input. services re-encode each one (D-047)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput(attrs={"accept": "image/*", "multiple": True}))
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(d, initial) for d in data if d]
        return [single(data, initial)] if data else []


class RequestForm(forms.Form):
    """Units come grouped by property; the service checks the unit belongs to the property."""

    property = forms.ModelChoiceField(label=_("Property"), queryset=None, to_field_name="public_id",
                                      empty_label=_("Choose…"))
    unit = forms.ChoiceField(label=_("Unit"), required=False)
    title = forms.CharField(label=_("What is wrong"), max_length=120,
                            widget=forms.TextInput(attrs={"placeholder": _("e.g. Kitchen sink is leaking")}))
    description = forms.CharField(label=_("Details (optional)"), max_length=2000, required=False,
                                  widget=forms.Textarea(attrs={"rows": 3}))
    kind = forms.ChoiceField(label=_("Kind"), choices=MaintenanceRequest.Kind.choices,
                             initial=MaintenanceRequest.Kind.OTHER)
    priority = forms.ChoiceField(label=_("Priority"), choices=MaintenanceRequest.Priority.choices,
                                 initial=MaintenanceRequest.Priority.NORMAL,
                                 help_text=_("Emergency: due in 24 hours. High: 3 days. Normal: 7 days. Low: 30 days."))
    from_tenant = forms.BooleanField(label=_("Reported by the tenant of the unit"), required=False,
                                     help_text=_("They follow it in the portal and get an SMS when it is fixed."))
    photos = PhotosField(label=_("Photos (optional)"))

    def __init__(self, *args, properties, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["property"].queryset = properties
        units = [(str(u.public_id), f"{p.name} · {u.code}") for p in properties for u in p.units.all()]
        self.fields["unit"].choices = [("", _("Common areas (no unit)")), *units]


class PortalRequestForm(forms.Form):
    title = forms.CharField(label=_("What is wrong"), max_length=120,
                            widget=forms.TextInput(attrs={"placeholder": _("e.g. No water in the bathroom")}))
    description = forms.CharField(label=_("Tell us more (optional)"), max_length=2000, required=False,
                                  widget=forms.Textarea(attrs={"rows": 3}))
    emergency = forms.BooleanField(label=_("It is an emergency"), required=False,
                                   help_text=_("A burst pipe, no power, a door that will not lock, a danger."))
    photos = PhotosField(label=_("Photos (optional, up to 3)"))


class AssignForm(forms.Form):
    assigned_to = forms.ChoiceField(label=_("Staff member"), required=False)
    supplier = forms.ModelChoiceField(label=_("Supplier"), queryset=None, required=False,
                                      to_field_name="public_id", empty_label=_("No supplier"))
    sms_supplier = forms.BooleanField(label=_("Send the supplier an SMS with the job"), required=False,
                                      initial=True)

    def __init__(self, *args, users, suppliers, **kwargs):
        super().__init__(*args, **kwargs)
        self.users = {str(u.public_id): u for u in users}
        self.fields["assigned_to"].choices = [("", _("Nobody")),
                                              *((k, str(u)) for k, u in self.users.items())]
        self.fields["supplier"].queryset = suppliers

    def clean_assigned_to(self):
        return self.users.get(self.cleaned_data["assigned_to"])


class PriorityForm(forms.Form):
    priority = forms.ChoiceField(label=_("Priority"), choices=MaintenanceRequest.Priority.choices)
    due_at = forms.DateTimeField(label=_("Due by"), required=False,
                                 widget=forms.DateTimeInput(attrs={"type": "datetime-local"},
                                                            format="%Y-%m-%dT%H:%M"),
                                 help_text=_("Leave as it is to set it from the priority."))


class NoteForm(forms.Form):
    text = forms.CharField(label=_("Note"), max_length=1000, required=False,
                           widget=forms.Textarea(attrs={"rows": 2}))
    share = forms.BooleanField(label=_("Tell the tenant (SMS)"), required=False)
    photos = PhotosField(label=_("Photos"))


class CostForm(ExpenseForm):
    """An expense for the repair: the property is the request's."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, properties=None, **kwargs)
        del self.fields["property"]
