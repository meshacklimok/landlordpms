from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import password_validators_help_text_html
from django.utils.translation import gettext_lazy as _

from core import ratelimit
from core.net import client_ip
from core.phone import InvalidPhoneNumber, normalize_phone

from .capabilities import CAPABILITIES
from .models import Organization, Role
from .permissions import visible_properties

LOGIN_LIMIT = 10  # attempts per 15 minutes, per identifier and per IP


class PhoneField(forms.CharField):
    def __init__(self, **kwargs):
        kwargs.setdefault("label", _("Phone number"))
        kwargs.setdefault("max_length", 20)
        kwargs.setdefault("widget", forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "tel",
                                                           "placeholder": "0712 345678"}))
        super().__init__(**kwargs)

    def clean(self, value):
        value = super().clean(value)
        if not value:
            return value
        try:
            return normalize_phone(value)
        except InvalidPhoneNumber as exc:
            raise forms.ValidationError(str(exc)) from None


class RegisterForm(forms.Form):
    full_name = forms.CharField(label=_("Full name"), max_length=150)
    phone = PhoneField()
    email = forms.EmailField(label=_("Email (optional)"), required=False)
    password = forms.CharField(label=_("Password"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
                               help_text=password_validators_help_text_html())
    accept_terms = forms.BooleanField(label=_("I accept the Terms of Service and Privacy Policy"))


class LoginForm(forms.Form):
    identifier = forms.CharField(label=_("Phone or email"), max_length=254,
                                 widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True}))
    password = forms.CharField(
        label=_("Password"), widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )

    def __init__(self, request, *args, **kwargs):
        self.request = request
        self.user = None
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        identifier = (data.get("identifier") or "").strip().lower()
        password = data.get("password")
        if not identifier or not password:
            return data
        ip = client_ip(self.request)
        # Key on the normalized phone so "0712…", "+254 712…" etc. share one counter.
        key = identifier
        if "@" not in identifier:
            try:
                key = normalize_phone(identifier)
            except InvalidPhoneNumber:
                pass
        if not (ratelimit.hit(f"login:id:{key}", LOGIN_LIMIT, 900)
                and ratelimit.hit(f"login:ip:{ip}", LOGIN_LIMIT * 5, 900)):
            raise forms.ValidationError(_("Too many attempts. Wait 15 minutes and try again."))
        self.user = authenticate(self.request, username=identifier, password=password)
        if self.user is None:
            raise forms.ValidationError(_("Phone/email or password is incorrect."))
        ratelimit.reset(f"login:id:{key}")
        return data


class OTPForm(forms.Form):
    code = forms.CharField(label=_("6-digit code"), max_length=6, min_length=6,
                           widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code",
                                                         "autofocus": True}))


class PasswordResetRequestForm(forms.Form):
    phone = PhoneField()


class PasswordResetConfirmForm(OTPForm):
    new_password = forms.CharField(label=_("New password"),
                                   widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
                                   help_text=password_validators_help_text_html())


class OrganizationForm(forms.Form):
    name = forms.CharField(label=_("Business or workspace name"), max_length=150)
    org_type = forms.ChoiceField(label=_("Type"), choices=Organization.Type.choices)


def capability_choices():
    return [(c.codename, c.description) for c in CAPABILITIES]


def grouped_capabilities():
    """[(module, [Cap, ...]), ...] in catalog order, for the role editor."""
    groups: dict[str, list] = {}
    for cap in CAPABILITIES:
        groups.setdefault(cap.module, []).append(cap)
    return list(groups.items())


class RoleForm(forms.Form):
    name = forms.CharField(label=_("Role name"), max_length=80)
    description = forms.CharField(label=_("Description"), max_length=255, required=False)
    capabilities = forms.MultipleChoiceField(choices=capability_choices, required=False,
                                             widget=forms.CheckboxSelectMultiple)


class CloneRoleForm(forms.Form):
    name = forms.CharField(label=_("New role name"), max_length=80)


class InviteForm(forms.Form):
    full_name = forms.CharField(label=_("Name"), max_length=150, required=False)
    phone = PhoneField()
    email = forms.EmailField(label=_("Email (optional)"), required=False)
    role = forms.ModelChoiceField(queryset=Role.objects.none(), label=_("Role"))
    all_properties = forms.BooleanField(label=_("Access to all properties"), required=False, initial=True)
    properties = forms.ModelMultipleChoiceField(queryset=None, required=False,
                                                widget=forms.CheckboxSelectMultiple, label=_("Properties"))

    def __init__(self, membership, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = Role.objects.for_org(membership.organization)
        self.fields["properties"].queryset = visible_properties(membership)


class MemberRoleForm(forms.Form):
    role = forms.ModelChoiceField(queryset=Role.objects.none(), label=_("Role"))

    def __init__(self, membership, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = Role.objects.for_org(membership.organization)


class MemberScopeForm(forms.Form):
    all_properties = forms.BooleanField(label=_("Access to all properties"), required=False)
    properties = forms.ModelMultipleChoiceField(queryset=None, required=False,
                                                widget=forms.CheckboxSelectMultiple, label=_("Properties"))

    def __init__(self, membership, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["properties"].queryset = visible_properties(membership)


class OverrideForm(forms.Form):
    STATE_CHOICES = [("", _("Follow role")), ("grant", _("Grant")), ("deny", _("Withhold"))]
    capability = forms.ChoiceField(choices=capability_choices)
    state = forms.ChoiceField(choices=STATE_CHOICES, required=False)

    def granted(self):
        return {"grant": True, "deny": False}.get(self.cleaned_data["state"])
