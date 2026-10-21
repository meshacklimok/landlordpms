from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Prospect, Viewing

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class UserChoiceField(forms.ChoiceField):
    """Staff chosen by public id from a list the view builds; cleans to the user or None."""

    def __init__(self, *args, empty_label=None, **kwargs):
        self.empty_label = empty_label
        super().__init__(*args, **kwargs)

    def set_users(self, users):
        self.users = {str(u.public_id): u for u in users}
        empty = [("", self.empty_label)] if self.empty_label is not None else []
        self.choices = [*empty, *((k, str(u)) for k, u in self.users.items())]

    def clean(self, value):
        value = super().clean(value)
        return self.users.get(value)


class ProspectForm(forms.Form):
    """Units come grouped by property; the view checks the unit belongs to the property."""

    property = forms.ModelChoiceField(label=_("Property"), queryset=None, to_field_name="public_id",
                                      empty_label=_("Choose…"))
    unit = forms.ChoiceField(label=_("Unit they asked about"), required=False)
    name = forms.CharField(label=_("Name"), max_length=150)
    phone = forms.CharField(label=_("Phone"), max_length=20,
                            widget=forms.TextInput(attrs={"inputmode": "tel", "placeholder": "0712 345678"}))
    email = forms.EmailField(label=_("Email (optional)"), required=False)
    source = forms.ChoiceField(label=_("How they found us"), choices=Prospect.Source.choices,
                               initial=Prospect.Source.WALK_IN)
    budget = forms.DecimalField(label=_("Budget per month (optional)"), required=False, min_value=0,
                                max_digits=12, decimal_places=2)
    move_in_by = forms.DateField(label=_("Wants to move in by (optional)"), required=False, widget=DATE)
    wants = forms.CharField(label=_("Looking for (optional)"), max_length=300, required=False,
                            widget=forms.TextInput(attrs={"placeholder": _("e.g. 2 bedrooms, ground floor, parking")}))
    next_contact_on = forms.DateField(label=_("Follow up on (optional)"), required=False, widget=DATE)
    assigned_to = UserChoiceField(label=_("Handled by"), required=False)
    note = forms.CharField(label=_("Note (optional)"), max_length=1000, required=False,
                           widget=forms.Textarea(attrs={"rows": 2}))
    confirm_duplicate = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, properties, users, editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["property"].queryset = properties
        units = [(str(u.public_id), f"{p.name} · {u.code}") for p in properties for u in p.units.all()]
        self.fields["unit"].choices = [("", _("Any unit")), *units]
        self.fields["assigned_to"].set_users(users)
        if editing:
            for name in ("property", "assigned_to", "note", "confirm_duplicate"):
                del self.fields[name]


class NoteForm(forms.Form):
    text = forms.CharField(label=_("Note"), max_length=1000, widget=forms.Textarea(attrs={"rows": 2}))
    contacted = forms.BooleanField(label=_("I spoke to them"), required=False)
    next_contact_on = forms.DateField(label=_("Follow up on"), required=False, widget=DATE,
                                      help_text=_("Leave empty if no follow-up is needed."))


class ViewingForm(forms.Form):
    unit = forms.ModelChoiceField(label=_("Unit"), queryset=None, to_field_name="public_id",
                                  empty_label=_("Choose…"))
    scheduled_at = forms.DateTimeField(label=_("When"),
                                       widget=forms.DateTimeInput(attrs={"type": "datetime-local"},
                                                                  format="%Y-%m-%dT%H:%M"))
    with_user = UserChoiceField(label=_("Shown by"), required=False, empty_label=_("Nobody yet"))

    def __init__(self, *args, units, users, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].queryset = units
        self.fields["with_user"].set_users(users)


class OutcomeForm(forms.Form):
    status = forms.ChoiceField(choices=[(Viewing.Status.DONE, Viewing.Status.DONE.label),
                                        (Viewing.Status.NO_SHOW, Viewing.Status.NO_SHOW.label),
                                        (Viewing.Status.CANCELLED, Viewing.Status.CANCELLED.label)])
    outcome = forms.CharField(max_length=300, required=False)


class HoldForm(forms.Form):
    unit = forms.ModelChoiceField(label=_("Unit"), queryset=None, to_field_name="public_id",
                                  empty_label=_("Choose…"))
    until = forms.DateField(label=_("Hold until"), widget=DATE,
                            help_text=_("At most %(n)s days. The unit shows as Reserved until then.")
                            % {"n": Prospect.MAX_HOLD_DAYS})

    def __init__(self, *args, units, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].queryset = units


class AssignForm(forms.Form):
    assigned_to = UserChoiceField(label=_("Handled by"))

    def __init__(self, *args, users, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].set_users(users)


class LoseForm(forms.Form):
    reason = forms.ChoiceField(label=_("Why"), choices=Prospect.LostReason.choices)
    note = forms.CharField(label=_("Note"), max_length=300, required=False)


class EnquiryForm(forms.Form):
    """The public form on a vacancy link. `website` is a trap for bots and must stay empty."""

    name = forms.CharField(label=_("Your name"), max_length=150)
    phone = forms.CharField(label=_("Your phone"), max_length=20,
                            widget=forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "tel",
                                                          "placeholder": "0712 345678"}))
    message = forms.CharField(label=_("Message (optional)"), max_length=500, required=False,
                              widget=forms.Textarea(attrs={"rows": 2,
                                                           "placeholder": _("e.g. When can I view it?")}))
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1",
                                                                             "autocomplete": "off"}))


class HostelEnquiryForm(forms.Form):
    """The public form on a hostel's booking page (D-189). `website` is a trap for bots and must stay empty."""

    name = forms.CharField(label=_("Your name"), max_length=150)
    phone = forms.CharField(label=_("Your phone"), max_length=20,
                            widget=forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "tel",
                                                          "placeholder": "0712 345678"}))
    gender = forms.ChoiceField(label=_("I am"), choices=[("", _("Choose")), ("F", _("Female")), ("M", _("Male"))])
    wants = forms.ChoiceField(label=_("I want"), choices=[("bed", _("A bed in a shared room")),
                                                           ("room", _("A whole room")),
                                                           ("any", _("Either"))], initial="bed")
    institution = forms.CharField(label=_("College or university"), max_length=120, required=False)
    move_in_by = forms.DateField(label=_("Moving in on"), required=False,
                                 widget=forms.DateInput(attrs={"type": "date"}))
    message = forms.CharField(label=_("Message (optional)"), max_length=500, required=False,
                              widget=forms.Textarea(attrs={"rows": 2}))
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1",
                                                                             "autocomplete": "off"}))
