from django import forms
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from accounts.forms import PhoneField
from accounts.models import Organization
from core.kenya import County
from core.models import EmailRouting
from growth.models import Article, ReferralPolicy
from subscriptions import services as billing
from subscriptions.models import BillingPolicy, Plan, Subscription

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


class LatePolicyForm(forms.ModelForm):
    """The platform's defaults for what stops while a payment is overdue and how the landlord is reminded (D-198)."""

    class Meta:
        model = BillingPolicy
        fields = [f"stop_{key}_days" for key in billing.STOPPABLE] + ["dunning_email", "dunning_sms"]
        widgets = {f"stop_{key}_days": forms.NumberInput(attrs={"min": 0, "max": 365}) for key in billing.STOPPABLE}


class LateRulesForm(forms.Form):
    """One organization's own late-payment rules. Empty = follow the platform, a number = its own days,
    "never" = it never stops for this organization."""

    reason = forms.CharField(label=_("Reason"), max_length=200, help_text=_("Kept in the audit trail."))

    def __init__(self, *args, sub=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.keys = list(billing.STOPPABLE)
        for key in self.keys:
            self.fields[key] = forms.CharField(label=billing.STOPPABLE[key], required=False, max_length=5)
        for key in billing.DUNNING:
            self.fields[key] = forms.ChoiceField(
                label=billing.DUNNING[key], required=False,
                choices=[("", _("Follow the platform")), ("on", _("Yes")), ("off", _("No"))])

    def clean(self):
        data = super().clean()
        out = {}
        for key in self.keys:
            raw = (data.get(key) or "").strip().lower()
            if not raw:
                out[key] = "default"
            elif raw in ("never", "n"):
                out[key] = None
            elif raw.isdigit() and int(raw) <= 365:
                out[key] = int(raw)
            else:
                self.add_error(key, _("Type a number of days (0 to 365), “never”, or leave it empty."))
        for key in billing.DUNNING:
            out[key] = {"": "default", "on": True, "off": False}.get(data.get(key) or "", "default")
        data["values"] = out
        return data


class CustomPriceForm(forms.Form):
    """One organization's own price on its current plan, before VAT. Empty = the plan's price."""

    monthly = forms.DecimalField(label=_("Monthly price (KES)"), required=False, min_value=0, max_value=10000000,
                                 max_digits=12, decimal_places=2)
    yearly = forms.DecimalField(label=_("Yearly price (KES)"), required=False, min_value=0, max_value=10000000,
                                max_digits=12, decimal_places=2)
    reason = forms.CharField(label=_("Reason"), max_length=200, help_text=_("Kept in the audit trail."))


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


class OrganizationAccessForm(forms.Form):
    """The owner's other per-organization controls (D-211): subscription by hand, grace, sign-out, one seat."""

    ACTIONS = ["subscription", "grace", "signout", "member"]
    action = forms.ChoiceField(choices=[(a, a) for a in ACTIONS], widget=forms.HiddenInput)
    plan = forms.ModelChoiceField(Plan.objects.all(), required=False, label=_("Plan"))
    status = forms.ChoiceField(required=False, choices=Subscription.Status.choices, label=_("Status"))
    interval = forms.ChoiceField(required=False, choices=Subscription.Interval.choices, label=_("Billed"))
    period_end = forms.DateField(required=False, label=_("Paid until"), widget=forms.DateInput(attrs={"type": "date"}))
    days = forms.IntegerField(required=False, min_value=1, max_value=90, label=_("Days"))
    user = forms.IntegerField(required=False, widget=forms.HiddenInput)
    active = forms.BooleanField(required=False)
    reason = forms.CharField(label=_("Reason"), max_length=200, help_text=_("Kept in the audit trail."))

    def clean(self):
        d = super().clean()
        act = d.get("action")
        if act == "subscription" and not (d.get("plan") and d.get("status") and d.get("interval")):
            raise forms.ValidationError(_("Choose the plan, status and billing interval."))
        if act == "grace" and not d.get("days"):
            raise forms.ValidationError(_("Give the number of days."))
        if act == "member" and not d.get("user"):
            raise forms.ValidationError(_("Choose the person."))
        return d


class LoginContactForm(forms.Form):
    user = forms.IntegerField(widget=forms.HiddenInput)
    phone = forms.CharField(label=_("New phone number"), required=False, max_length=20)
    email = forms.EmailField(label=_("New email"), required=False)
    reset_mfa = forms.BooleanField(label=_("Also reset two-step login (they lost the authenticator too)"),
                                   required=False)
    reason = forms.CharField(label=_("How you checked it is them"), max_length=200,
                             help_text=_("Kept in the audit trail."))


class AnnouncementForm(forms.Form):
    message = forms.CharField(label=_("Message"), max_length=300, widget=forms.Textarea(attrs={"rows": 2}))
    level = forms.ChoiceField(label=_("Kind"), choices=PlatformAnnouncement.Level.choices)
    starts_at = forms.DateTimeField(label=_("Shows from (optional, now if empty)"), required=False,
                                    widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
                                    input_formats=["%Y-%m-%dT%H:%M"])
    ends_at = forms.DateTimeField(label=_("Ends (optional, one month from now if empty)"), required=False,
                                  widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
                                  input_formats=["%Y-%m-%dT%H:%M"])
    organizations = forms.ModelMultipleChoiceField(
        label=_("Send to"), required=False, queryset=Organization.objects.none(),
        help_text=_("Pick one or more organizations. Leave empty to send to everyone."))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["organizations"].queryset = Organization.objects.order_by("name")

    @classmethod
    def for_item(cls, item):
        local = timezone.localtime(item.ends_at).strftime("%Y-%m-%dT%H:%M") if item.ends_at else ""
        start = timezone.localtime(item.starts_at).strftime("%Y-%m-%dT%H:%M") if item.starts_at else ""
        return cls(initial={"message": item.message, "level": item.level, "ends_at": local, "starts_at": start,
                            "organizations": list(item.organizations.values_list("pk", flat=True))})


class AuditFilterForm(forms.Form):
    action = forms.CharField(required=False, max_length=64)
    organization = forms.CharField(required=False, max_length=100)
    actor = forms.CharField(required=False, max_length=100)
    since = forms.DateField(required=False)
    until = forms.DateField(required=False)


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
    segment = forms.ChoiceField(required=False, label=_("Group"))
    step = forms.ChoiceField(required=False, label=_("Stuck at"))
    county = forms.ChoiceField(required=False, label=_("County"))
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
        self.fields["segment"].choices = [("", _("Any group")), *owners.SEGMENTS.items()]
        self.fields["county"].choices = [("", _("Any county")), *County.choices]
        self.fields["step"].choices = [("", _("Any setup step")), *owners.STEPS.items()]
        self.fields["sort"].choices = list(owners.SORTS.items())


class OwnerEmailForm(forms.Form):
    subject = forms.CharField(label=_("Subject"), max_length=200)
    body = forms.CharField(label=_("Message"), max_length=5000, widget=forms.Textarea(attrs={"rows": 10}),
                           help_text=_("Write {name} for their first name and {organization} for their organization."))
    checked = forms.BooleanField(label=_("I have checked who this goes to"), required=False)


class OwnerCreateForm(forms.Form):
    full_name = forms.CharField(label=_("Full name"), max_length=150)
    phone = PhoneField()
    email = forms.EmailField(label=_("Email"), required=False)
    organization = forms.CharField(label=_("Organization name"), max_length=150)
    org_type = forms.ChoiceField(label=_("Kind of organization"), choices=Organization.Type.choices)


class OwnerEditForm(forms.Form):
    full_name = forms.CharField(label=_("Full name"), max_length=150)
    phone = PhoneField()
    email = forms.EmailField(label=_("Email"), required=False)
    organization = forms.CharField(label=_("Organization name"), max_length=150)


class OwnerNoteForm(forms.Form):
    body = forms.CharField(label=_("Note"), max_length=2000, widget=forms.Textarea(attrs={"rows": 3}))
    follow_up_on = forms.DateField(label=_("Follow up on"), required=False,
                                   widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))


class ArticleForm(forms.ModelForm):
    """A public article. The address is made from the title when left empty."""

    class Meta:
        model = Article
        fields = ["title", "slug", "category", "summary", "body", "author", "featured"]
        widgets = {"body": forms.Textarea(attrs={"rows": 24, "class": "font-monospace"}),
                   "summary": forms.Textarea(attrs={"rows": 2})}
        help_texts = {"slug": _("Leave empty to make it from the title. Changing it later breaks links people have already shared.")}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False

    def clean(self):
        data = super().clean()
        if not data.get("slug") and data.get("title"):
            base = slugify(data["title"])[:70] or "article"
            slug, n = base, 2
            taken = Article.objects.exclude(pk=self.instance.pk)
            while taken.filter(slug=slug).exists():
                slug, n = f"{base}-{n}", n + 1
            data["slug"] = slug
        return data
