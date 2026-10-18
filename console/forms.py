from django import forms
from django.utils.translation import gettext_lazy as _

from accounts.forms import PhoneField
from accounts.models import Organization
from core.models import EmailRouting
from growth.models import ReferralPolicy

from . import access
from .models import PlatformAnnouncement, PlatformExpense, PlatformStaff


class EmailRoutingForm(forms.ModelForm):
    class Meta:
        model = EmailRouting
        fields = ["system_account", "messages_account"]


class ReferralPolicyForm(forms.ModelForm):
    class Meta:
        model = ReferralPolicy
        fields = ["reward_kind", "discount_percent", "reward_days", "reward_referred_too", "referred_reward_kind",
                  "referred_discount_percent", "referred_reward_days", "require_referrer_paid", "require_payment",
                  "min_properties", "min_units", "min_tenants", "min_leases", "min_invoices", "max_rewards_per_year"]


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
    level = forms.ChoiceField(label=_("Kind"), choices=PlatformAnnouncement.Level.choices)
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


class NotificationTypeForm(forms.Form):
    """The platform-wide setting of one notification (the code default when never saved)."""

    enabled = forms.BooleanField(label=_("On by default"), required=False)
    channels = forms.CharField(label=_("Channels, in order"), required=False,
                               help_text=_("e.g. WHATSAPP, SMS, EMAIL. Only channels it has wording for."))
    offsets = forms.CharField(label=_("Reminder days"), required=False,
                              help_text=_("Whole days, e.g. 7, 3. Only for reminders sent relative to a date."))
    mandatory = forms.BooleanField(label=_("Required"), required=False,
                                   help_text=_("Organizations cannot switch it off; recipients cannot opt out."))
    urgent = forms.BooleanField(label=_("Urgent"), required=False,
                                help_text=_("Sent during quiet hours instead of waiting."))

    def __init__(self, base, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.base = base

    def clean_channels(self):
        channels = [c.strip().upper() for c in self.cleaned_data["channels"].replace(";", ",").split(",") if c.strip()]
        bad = [c for c in channels if c not in self.base.channels]
        if bad:
            raise forms.ValidationError(_("No wording for: %(bad)s. Available: %(ok)s.") % {
                "bad": ", ".join(bad), "ok": ", ".join(self.base.channels)})
        return list(dict.fromkeys(channels))

    def clean_offsets(self):
        raw = self.cleaned_data["offsets"].replace(";", ",")
        try:
            days = sorted({int(x) for x in raw.split(",") if x.strip()}, reverse=True)
        except ValueError:
            raise forms.ValidationError(_("Enter whole days separated by commas, e.g. 7, 3.")) from None
        if days and not self.base.offsets:
            raise forms.ValidationError(_("This notification is not sent relative to a date."))
        if any(d < -365 or d > 365 for d in days) or len(days) > 6:
            raise forms.ValidationError(_("At most 6 days, each within a year."))
        return days


class OwnerFilterForm(forms.Form):
    q = forms.CharField(required=False, label=_("Search"), max_length=80)
    status = forms.ChoiceField(required=False, label=_("Status"))
    plan = forms.ChoiceField(required=False, label=_("Plan"))
    org_type = forms.ChoiceField(required=False, label=_("Kind"))
    activity = forms.ChoiceField(required=False, label=_("Activity"))
    email = forms.ChoiceField(required=False, label=_("Email"))
    sort = forms.ChoiceField(required=False, label=_("Order"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from . import owners

        self.fields["status"].choices = [("", _("Any status")), *Organization.Status.choices]
        self.fields["plan"].choices = owners.plan_choices()
        self.fields["org_type"].choices = [("", _("Any kind")), *Organization.Type.choices]
        self.fields["activity"].choices = [("", _("Any activity")), *owners.ACTIVITY.items()]
        self.fields["email"].choices = [("", _("With or without email")), ("has", _("Has an email")),
                                        ("none", _("No email"))]
        self.fields["sort"].choices = list(owners.SORTS.items())


class OwnerEmailForm(forms.Form):
    subject = forms.CharField(label=_("Subject"), max_length=200)
    body = forms.CharField(label=_("Message"), max_length=5000, widget=forms.Textarea(attrs={"rows": 10}),
                           help_text=_("Write {name} for their first name and {organization} for their organization."))
    checked = forms.BooleanField(label=_("I have checked who this goes to"), required=False)
