from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import password_validators_help_text_html
from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _

from billing.models import DepositEntry
from core import ratelimit
from core.net import client_ip
from core.phone import InvalidPhoneNumber, normalize_phone

from .capabilities import CAPABILITIES
from .models import Branch, Organization, Role
from .permissions import branch_ids, visible_properties

DEPOSIT_RULE_FIELDS = ("deposit_refund_days", "deposit_deduction_headings", "deposit_proof_required",
                       "deposit_allow_offset")
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
    email = forms.EmailField(label=_("Email"), help_text=_("We email you a code to confirm it. You can also log in "
                                                           "with it."))
    password = forms.CharField(label=_("Password"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
                               help_text=password_validators_help_text_html())
    accept_terms = forms.BooleanField(label=_("I accept the Terms of Service and Privacy Policy"))

    def __init__(self, *args, email_optional: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if email_optional:  # invited as a caretaker or maintenance staff
            self.fields["email"].required = False
            self.fields["email"].label = _("Email (optional)")


class EmailForm(forms.Form):
    email = forms.EmailField(label=_("Email"), help_text=_("We email you a code to confirm it."))


class LoginForm(forms.Form):
    identifier = forms.CharField(label=_("Email or phone number"), max_length=254,
                                 widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True,
                                                               "placeholder": "you@example.com"}))
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
    identifier = forms.CharField(
        label=_("Email"), max_length=254,
        help_text=_("We email you a code. A caretaker with no email enters their phone number and gets it by SMS."),
        widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True,
                                      "placeholder": "you@example.com"}))

    def clean_identifier(self):
        value = self.cleaned_data["identifier"].strip()
        if "@" in value:
            try:
                forms.EmailField().clean(value)
            except forms.ValidationError:
                raise forms.ValidationError(_("Enter a valid email address.")) from None
            return value.lower()
        try:
            return normalize_phone(value)
        except InvalidPhoneNumber as exc:
            raise forms.ValidationError(str(exc)) from None


class PasswordResetConfirmForm(OTPForm):
    new_password = forms.CharField(label=_("New password"),
                                   widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
                                   help_text=password_validators_help_text_html())


class OrganizationForm(forms.Form):
    name = forms.CharField(label=_("Business or workspace name"), max_length=150)
    org_type = forms.ChoiceField(label=_("Type"), choices=Organization.Type.choices)


class OrganizationSettingsForm(forms.Form):
    """The organization page (D-123): who you are on paper and how rent is billed."""

    name = forms.CharField(label=_("Business or workspace name"), max_length=150)
    org_type = forms.ChoiceField(label=_("Type"), choices=Organization.Type.choices)
    brand_name = forms.CharField(
        label=_("Name tenants see"), max_length=150, required=False,
        help_text=_("Printed on letters and notices instead of the name above. Leave blank to use that name."))
    billing_phone = PhoneField(label=_("Office phone"), required=False,
                               help_text=_("Printed on notices so tenants know whom to call."))
    billing_email = forms.EmailField(label=_("Office email"), required=False,
                                     widget=forms.EmailInput(attrs={"placeholder": "office@example.co.ke"}))
    document_footer = forms.CharField(
        label=_("Footer on letters"), max_length=200, required=False,
        help_text=_("A line at the bottom of letters and notices, e.g. your address or registration number."))
    address = forms.CharField(label=_("Address"), max_length=200, required=False,
                              help_text=_("Printed under your name on invoices, e.g. Kimathi House, Nairobi."))
    logo = forms.ImageField(label=_("Logo"), required=False, widget=forms.FileInput(attrs={"accept": "image/png,image/jpeg"}),
                            help_text=_("PNG or JPG, up to 1 MB. Printed at the top of invoices, receipts, letters, agreements and statements."))
    remove_logo = forms.BooleanField(label=_("Remove the logo"), required=False)
    stamp = forms.ImageField(label=_("Signature or stamp"), required=False,
                             widget=forms.FileInput(attrs={"accept": "image/png,image/jpeg"}),
                             help_text=_("PNG or JPG, up to 1 MB. Printed above \"Authorised signature\" at the end of invoices."))
    remove_stamp = forms.BooleanField(label=_("Remove the signature or stamp"), required=False)
    brand_color = forms.CharField(
        label=_("Invoice colour"), max_length=7, required=False,
        validators=[RegexValidator(r"^#[0-9A-Fa-f]{6}$", _("Use a hex colour like #1A73E8."))],
        widget=forms.TextInput(attrs={"placeholder": "#1D4E89"}), help_text=_("Leave blank for the standard blue."))
    invoice_payment_info = forms.CharField(
        label=_("How to pay, on invoices"), max_length=500, required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("E.g. Paybill 123456, or bank, branch and account number. Printed with the account number."))
    invoice_terms = forms.CharField(
        label=_("Terms on invoices"), max_length=600, required=False, widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("E.g. rent is due by the 5th; a late fee applies after that."))
    invoice_show_tenant_contact = forms.BooleanField(label=_("Print the tenant's phone and email on invoices"),
                                                     required=False)
    invoice_show_previous_balance = forms.BooleanField(
        label=_("Show earlier unpaid invoices and the total owing"), required=False)
    invoice_lead_days = forms.IntegerField(
        label=_("Send invoices this many days before the month starts"), min_value=0, max_value=28,
        widget=forms.NumberInput(attrs={"inputmode": "numeric"}))
    holds_deposits = forms.BooleanField(
        label=_("We hold deposits"), required=False,
        help_text=_("For example a water connection deposit. Adds deposit records to each account."))
    prorate_partial_months = forms.BooleanField(
        label=_("Charge part months by the day (first and last month of a lease)"), required=False)
    late_fee_mode = forms.ChoiceField(
        label=_("Late fees"), choices=Organization.LateFeeMode.choices,
        help_text=_("Each fee waits on the Late fees page until staff charge or skip it. Only invoices that go "
                    "overdue after you switch this on get one."))
    late_fee_amount = forms.DecimalField(
        label=_("Fixed fee"), min_value=0, max_digits=14, decimal_places=2, required=False,
        widget=forms.NumberInput(attrs={"inputmode": "decimal", "placeholder": "500"}))
    late_fee_percent = forms.DecimalField(
        label=_("Percentage of what is unpaid"), min_value=0, max_value=100, max_digits=5, decimal_places=2,
        required=False, widget=forms.NumberInput(attrs={"inputmode": "decimal", "placeholder": "5"}))
    late_fee_cap = forms.DecimalField(
        label=_("Most one fee may come to"), min_value=0, max_digits=14, decimal_places=2, required=False,
        help_text=_("Leave blank for no limit."), widget=forms.NumberInput(attrs={"inputmode": "decimal"}))
    # How deposits are cleared (D-150). Kenya sets no deadline or list of deductions, so the owner decides.
    deposit_refund_days = forms.IntegerField(
        label=_("Settle a deposit within this many days of the lease ending"), min_value=0, max_value=365,
        required=False,
        widget=forms.NumberInput(attrs={"inputmode": "numeric"}),
        help_text=_("Staff see when a refund or a statement of deductions is due, and when it is overdue."))
    deposit_deduction_headings = forms.MultipleChoiceField(
        label=_("Deductions may be made for"), required=False, choices=DepositEntry.Heading.choices,
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Tick the reasons staff may use. Tick none to let them give any reason."))
    deposit_proof_required = forms.BooleanField(
        label=_("A deduction needs a receipt, quote or photo, or the move-out report"), required=False)
    deposit_allow_offset = forms.BooleanField(
        label=_("A deduction may be put towards rent the tenant owes"), required=False)

    def _clean_picture(self, name):
        pic = self.cleaned_data.get(name)
        if pic and pic.size > 1024 * 1024:
            raise forms.ValidationError(_("The picture is over 1 MB. Use a smaller one."))
        if pic and getattr(pic, "image", None) and pic.image.format not in ("PNG", "JPEG"):
            raise forms.ValidationError(_("Use a PNG or JPG picture."))
        return pic

    def clean(self):
        data = super().clean()
        if data.get("deposit_refund_days") is None:  # left blank: keep the number already set
            data.pop("deposit_refund_days", None)
        return data

    def clean_logo(self):
        return self._clean_picture("logo")

    def clean_stamp(self):
        return self._clean_picture("stamp")

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Landlords always hold deposits, so only the other kinds of billers are asked (D-130).
        if organization is None or organization.bills_rent:
            del self.fields["holds_deposits"]
        if organization is not None and not organization.takes_deposits:
            for name in DEPOSIT_RULE_FIELDS:
                del self.fields[name]


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


def grantable_branches(membership):
    """Branches this member may give: every live one when organization-wide, else their own (D-127)."""
    qs = Branch.objects.for_org(membership.organization)
    return qs if membership.all_properties else qs.filter(pk__in=branch_ids(membership))


class _ScopeFields(forms.Form):
    """Properties one by one, and whole branches (D-127). The branch field goes when there are none to give."""

    properties = forms.ModelMultipleChoiceField(queryset=None, required=False,
                                                widget=forms.CheckboxSelectMultiple, label=_("Properties"))
    branches = forms.ModelMultipleChoiceField(
        queryset=None, required=False, widget=forms.CheckboxSelectMultiple, label=_("Branches"),
        help_text=_("Every property in the branch, including ones added later."))

    def __init__(self, membership, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["properties"].queryset = visible_properties(membership)
        branches = grantable_branches(membership)
        if branches.exists():
            self.fields["branches"].queryset = branches
        else:
            del self.fields["branches"]


class InviteForm(_ScopeFields):
    full_name = forms.CharField(label=_("Name"), max_length=150, required=False)
    phone = PhoneField()
    email = forms.EmailField(label=_("Email (optional)"), required=False,
                             help_text=_("They add their own when they sign up. Every role but Caretaker and "
                                         "Maintenance Staff needs one."))
    role = forms.ModelChoiceField(queryset=Role.objects.none(), label=_("Role"))
    all_properties = forms.BooleanField(label=_("Access to all properties"), required=False, initial=True)

    field_order = ["full_name", "phone", "email", "role", "all_properties", "branches", "properties"]

    def __init__(self, membership, *args, **kwargs):
        super().__init__(membership, *args, **kwargs)
        self.fields["role"].queryset = Role.objects.for_org(membership.organization)


class MemberRoleForm(forms.Form):
    role = forms.ModelChoiceField(queryset=Role.objects.none(), label=_("Role"))

    def __init__(self, membership, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].queryset = Role.objects.for_org(membership.organization)


class MemberScopeForm(_ScopeFields):
    all_properties = forms.BooleanField(label=_("Access to all properties"), required=False)

    field_order = ["all_properties", "branches", "properties"]


class BranchForm(forms.ModelForm):
    """An agency office (D-127)."""

    class Meta:
        model = Branch
        fields = ["name", "phone", "email"]
        labels = {"name": _("Branch name"), "phone": _("Office phone (optional)"),
                  "email": _("Office email (optional)")}
        widgets = {"phone": forms.TextInput(attrs={"inputmode": "tel", "placeholder": "0712 345678"})}

    def _post_clean(self):
        # The service checks and saves.
        pass


class OverrideForm(forms.Form):
    STATE_CHOICES = [("", _("Follow role")), ("grant", _("Grant")), ("deny", _("Withhold"))]
    capability = forms.ChoiceField(choices=capability_choices)
    state = forms.ChoiceField(choices=STATE_CHOICES, required=False)

    def granted(self):
        return {"grant": True, "deny": False}.get(self.cleaned_data["state"])


class MFACodeForm(forms.Form):
    """A code from the authenticator app, or one of the recovery codes (D-059)."""

    code = forms.CharField(label=_("Code from your app"), max_length=20,
                           help_text=_("Lost your phone? Enter one of your recovery codes instead."),
                           widget=forms.TextInput(attrs={"inputmode": "text", "autocomplete": "one-time-code",
                                                         "autofocus": True, "autocapitalize": "off"}))


class MFADisableForm(forms.Form):
    password = forms.CharField(label=_("Password"),
                               widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    code = forms.CharField(label=_("Code from your app"), max_length=20,
                           widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "autocapitalize": "off"}))
