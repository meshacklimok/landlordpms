from django import forms
from django.utils.translation import gettext_lazy as _

from .models import WRITE_SCOPES, ApiKey, WebhookEndpoint
from .services import EXPIRY_DAYS


class ApiKeyForm(forms.Form):
    name = forms.CharField(label=_("Name"), max_length=60, help_text=_("e.g. Accounting sync"))
    scopes = forms.MultipleChoiceField(label=_("May read"), required=False,
                                       choices=[c for c in ApiKey.Scope.choices if c[0] not in WRITE_SCOPES],
                                       widget=forms.CheckboxSelectMultiple)
    write_scopes = forms.MultipleChoiceField(
        label=_("May change"), required=False, choices=[c for c in ApiKey.Scope.choices if c[0] in WRITE_SCOPES],
        widget=forms.CheckboxSelectMultiple, help_text=_("Leave empty for a key that only reads."))
    expires_in_days = forms.TypedChoiceField(
        label=_("Expires"), coerce=int, empty_value=None, required=False,
        choices=[("", _("Never"))] + [(d, _("In %(n)s days") % {"n": d}) for d in EXPIRY_DAYS],
        widget=forms.Select(attrs={"class": "form-select"}))


    def clean(self):
        cleaned = super().clean()
        cleaned["scopes"] = [*cleaned.get("scopes", []), *cleaned.pop("write_scopes", [])]
        return cleaned


class WebhookForm(forms.Form):
    url = forms.URLField(label=_("Address"), max_length=300, assume_scheme="https",
                         help_text=_("An https:// address on your server, e.g. https://example.com/hooks/lpms"))
    events = forms.MultipleChoiceField(label=_("Send"), choices=WebhookEndpoint.Event.choices,
                                       widget=forms.CheckboxSelectMultiple)


class WebhookEventsForm(forms.Form):
    events = forms.MultipleChoiceField(label=_("Send"), choices=WebhookEndpoint.Event.choices,
                                       widget=forms.CheckboxSelectMultiple)
