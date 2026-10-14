import datetime

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.forms import PhoneField

from .models import DemoRequest


class DemoRequestForm(forms.ModelForm):
    full_name = forms.CharField(label=_("Your name"), max_length=150)
    phone = PhoneField()
    email = forms.EmailField(label=_("Email (optional)"), required=False)
    organization_name = forms.CharField(label=_("Business or property name (optional)"), max_length=150,
                                        required=False)
    preferred_date = forms.DateField(label=_("Day that suits you"),
                                     widget=forms.DateInput(attrs={"type": "date"}))
    message = forms.CharField(label=_("Anything you would like us to cover (optional)"), required=False,
                              max_length=1000, widget=forms.Textarea(attrs={"rows": 3}))
    # Real visitors never see this; a script that fills every field does.
    website = forms.CharField(required=False, label="", widget=forms.TextInput(attrs={
        "tabindex": "-1", "autocomplete": "off", "aria-hidden": "true", "class": "visually-hidden"}))

    class Meta:
        model = DemoRequest
        fields = ["full_name", "phone", "email", "organization_name", "kind", "size", "channel", "preferred_date",
                  "preferred_slot", "message"]
        labels = {"kind": _("What do you manage?"), "size": _("How many units?"),
                  "channel": _("How would you like the demo?"), "preferred_slot": _("Time of day")}

    def clean_preferred_date(self):
        day = self.cleaned_data["preferred_date"]
        today = timezone.localdate()
        if day < today:
            raise forms.ValidationError(_("Choose today or a later day."))
        if day > today + datetime.timedelta(days=45):
            raise forms.ValidationError(_("Choose a day in the next 45 days."))
        return day
