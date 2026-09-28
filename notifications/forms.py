from django import forms
from django.utils.translation import gettext_lazy as _

from . import catalog
from .selectors import STATES


class LogFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=100)
    type = forms.ChoiceField(required=False, choices=[("", _("All notifications")), *catalog.CHOICES])
    status = forms.ChoiceField(required=False, choices=[("", "")] + [(k, k) for k in STATES])

    def filters(self) -> dict:
        return {k: v for k, v in (self.cleaned_data if self.is_bound and self.is_valid() else {}).items() if v}


class RuleForm(forms.Form):
    enabled = forms.BooleanField(required=False)
    offsets = forms.CharField(required=False, max_length=30)
    include_co_tenants = forms.BooleanField(required=False)


def _time_input():
    return forms.TimeInput(attrs={"type": "time", "class": "form-control"}, format="%H:%M")


class QuietHoursForm(forms.Form):
    start = forms.TimeField(label=_("From"), widget=_time_input())
    end = forms.TimeField(label=_("Until"), widget=_time_input())


class TemplateForm(forms.Form):
    body = forms.CharField(label=_("Text"), max_length=1000, widget=forms.Textarea(attrs={"rows": 5}))


class TenantChannelForm(forms.Form):
    allowed = forms.BooleanField(required=False)
    note = forms.CharField(required=False, max_length=200)
