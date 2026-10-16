from django import forms
from django.utils.translation import gettext_lazy as _

from . import catalog
from .selectors import STATES


class LogFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=100)
    type = forms.ChoiceField(required=False, choices=[("", _("All notifications")), *catalog.CHOICES])
    status = forms.ChoiceField(required=False, choices=[("", "")] + [(k, k) for k in STATES])
    channel = forms.ChoiceField(required=False, choices=[("", _("All channels")), *catalog.CHANNEL_CHOICES[:3]])
    date_from = forms.DateField(required=False)
    date_to = forms.DateField(required=False)

    def filters(self) -> dict:
        return {k: v for k, v in (self.cleaned_data if self.is_bound and self.is_valid() else {}).items() if v}


class InboxFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=100)
    type = forms.ChoiceField(required=False, choices=[("", _("All notifications")), *catalog.CHOICES])
    unread = forms.BooleanField(required=False)

    def filters(self) -> dict:
        return {k: v for k, v in (self.cleaned_data if self.is_bound and self.is_valid() else {}).items() if v}


class RuleForm(forms.Form):
    enabled = forms.BooleanField(required=False)
    email = forms.BooleanField(required=False)
    offsets = forms.CharField(required=False, max_length=30)
    include_co_tenants = forms.BooleanField(required=False)


def _time_input():
    return forms.TimeInput(attrs={"type": "time", "class": "form-control"}, format="%H:%M")


class QuietHoursForm(forms.Form):
    start = forms.TimeField(label=_("From"), widget=_time_input())
    end = forms.TimeField(label=_("Until"), widget=_time_input())


class ReplyEmailForm(forms.Form):
    reply_email = forms.EmailField(label=_("Replies go to"), required=False,
                                   widget=forms.EmailInput(attrs={"class": "form-control",
                                                                  "placeholder": "office@example.co.ke"}))


class WhatsAppAccountForm(forms.Form):
    phone_number_id = forms.CharField(
        label=_("Phone number ID"), max_length=30,
        widget=forms.TextInput(attrs={"class": "form-control", "inputmode": "numeric", "autocomplete": "off"}))
    access_token = forms.CharField(
        label=_("Access token"), required=False, max_length=1000,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "off"}, render_value=False))
    app_secret = forms.CharField(
        label=_("App secret"), required=False, max_length=200,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "off"}, render_value=False))
    enabled = forms.BooleanField(label=_("Send my WhatsApp messages from this number"), required=False,
                                 initial=True)


class EmailAccountForm(forms.Form):
    host = forms.CharField(label=_("Mail server"), max_length=200,
                           widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}))
    port = forms.IntegerField(label=_("Port"), min_value=1, max_value=65535, initial=587,
                              widget=forms.NumberInput(attrs={"class": "form-control"}))
    username = forms.CharField(label=_("Username"), required=False, max_length=200,
                               widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}))
    password = forms.CharField(
        label=_("Password"), required=False, max_length=500,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}, render_value=False))
    use_tls = forms.BooleanField(label=_("Use TLS (port 587)"), required=False, initial=True)
    from_email = forms.EmailField(label=_("Send from"), widget=forms.EmailInput(attrs={"class": "form-control"}))
    from_name = forms.CharField(label=_("Sender name"), required=False, max_length=100,
                                widget=forms.TextInput(attrs={"class": "form-control"}))
    enabled = forms.BooleanField(label=_("Send my email from this server"), required=False, initial=True)


class SmsAccountForm(forms.Form):
    username = forms.CharField(label=_("Africa's Talking username"), max_length=60,
                               widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}))
    api_key = forms.CharField(
        label=_("API key"), required=False, max_length=300,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}, render_value=False))
    sender_id = forms.CharField(label=_("Sender name"), required=False, max_length=11,
                                widget=forms.TextInput(attrs={"class": "form-control"}))
    enabled = forms.BooleanField(label=_("Send my SMS from this account"), required=False, initial=True)


class TemplateForm(forms.Form):
    body = forms.CharField(label=_("Text"), max_length=1000, widget=forms.Textarea(attrs={"rows": 5}))


class TenantChannelForm(forms.Form):
    allowed = forms.BooleanField(required=False)
    note = forms.CharField(required=False, max_length=200)


def _checks():
    return forms.CheckboxSelectMultiple(attrs={"class": "form-check-input"})


class AnnouncementForm(forms.Form):
    """Who a notice goes to and what it says (D-044 item 15). Filters left empty do not narrow."""

    text = forms.CharField(label=_("Message"), max_length=1000, widget=forms.Textarea(attrs={"rows": 4}))
    properties = forms.ModelMultipleChoiceField(label=_("Properties"), queryset=None, required=False,
                                                widget=_checks())
    buildings = forms.ModelMultipleChoiceField(label=_("Buildings"), queryset=None, required=False,
                                               widget=_checks())
    owing = forms.BooleanField(label=_("Only tenants owing money"), required=False)
    owing_days = forms.IntegerField(label=_("Overdue for at least (days)"), min_value=1, max_value=365,
                                    required=False, initial=1)
    ending = forms.BooleanField(label=_("Only leases ending soon"), required=False)
    ending_within = forms.IntegerField(label=_("Ending within (days)"), min_value=0, max_value=365,
                                       required=False, initial=60)
    include_co_tenants = forms.BooleanField(label=_("Also send to co-tenants"), required=False, initial=True)
    urgent = forms.BooleanField(label=_("Send now, even during quiet hours"), required=False)
    nonce = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, membership, **kwargs):
        from accounts.permissions import visible_properties
        from properties.models import Building

        from .announcements import can_target_owing

        super().__init__(*args, **kwargs)
        props = visible_properties(membership).order_by("name")
        self.fields["properties"].queryset = props
        self.fields["buildings"].queryset = (Building.objects.filter(property__in=props)
                                             .select_related("property").order_by("property__name", "name"))
        self.fields["buildings"].label_from_instance = lambda b: f"{b.property.name} · {b.name}"
        if not can_target_owing(membership):
            del self.fields["owing"], self.fields["owing_days"]

    def clean(self):
        data = super().clean()
        if data.get("owing") and not data.get("owing_days"):
            data["owing_days"] = 1
        if data.get("ending") and data.get("ending_within") is None:
            self.add_error("ending_within", _("Enter a number of days."))
        return data

    def audience(self):
        from .announcements import Audience

        data = self.cleaned_data
        return Audience(
            properties=tuple(data.get("properties") or ()), buildings=tuple(data.get("buildings") or ()),
            owing_days=data.get("owing_days") if data.get("owing") else None,
            ending_within=data.get("ending_within") if data.get("ending") else None,
            include_co_tenants=data.get("include_co_tenants", False))


class ComposeForm(forms.Form):
    """A message staff write for one tenant (D-125). The subject is for email only."""

    nonce = forms.UUIDField(widget=forms.HiddenInput)
    subject = forms.CharField(label=_("Subject"), required=False, max_length=200)
    text = forms.CharField(label=_("Message"), max_length=5000, widget=forms.Textarea(attrs={"rows": 10}))
    urgent = forms.BooleanField(label=_("Send now, even during quiet hours"), required=False)
