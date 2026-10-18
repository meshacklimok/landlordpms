"""Thin views: forms in, services do the work (D-009)."""

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy
from django.views import View
from django.views.decorators.debug import sensitive_post_parameters
from django.views.generic import TemplateView

from audit import services as audit
from core import ratelimit, reportcache
from core.net import client_ip
from properties.models import Property
from reports import home as reports_home

from . import branches, data_export, forms, identity, mfa, services, setup
from .capabilities import OWNER_CRITICAL, email_required_for
from .middleware import SESSION_KEY
from .mixins import CapabilityRequiredMixin, OrgMemberRequiredMixin, VerifiedUserRequiredMixin
from .models import Branch, DataExport, Invitation, Membership, Role, TOTPDevice
from .otp import OTPError
from .permissions import can, effective_capabilities

RESET_PHONE_KEY = "reset_phone"
INVITE_TOKEN_KEY = "invite_token"  # noqa: S105 - a session key name
PORTAL_INVITE_KEY = "portal_invite_token"  # set by portal.views.AcceptView
OWNER_INVITE_KEY = "owner_invite_token"  # noqa: S105 - set by owner_portal.views.AcceptView
# Sign-ups and reset codes per IP per hour (D-107). Generous, since mobile networks put many phones behind one
# address; each phone number has its own tighter limit on codes (accounts.otp).
SIGNUP_PER_IP_HOUR = 20
RESET_PER_IP_HOUR = 20


def _ip_allowed(request, action: str, limit: int) -> bool:
    return ratelimit.hit(f"{action}:ip:{client_ip(request)}", limit, 3600)


def _too_many_text() -> str:
    return _("Too many tries from this network. Wait an hour and try again.")


def _error_text(exc: ValidationError) -> str:
    return " ".join(exc.messages)


def _apply_errors(form, exc: ValidationError) -> None:
    if hasattr(exc, "error_dict"):
        for name, errors in exc.error_dict.items():
            form.add_error(name if name in form.fields else None, errors)
    else:
        form.add_error(None, exc)


# ---------------------------------------------------------------------------
# Sign-up, verification, login, password reset
# ---------------------------------------------------------------------------


def _invited(request) -> str:
    """What to tell a visitor whose referral code is waiting in the session (D-133); empty for none."""
    from growth import referrals

    return referrals.invite_offer(request)


def _typed_referral_code(request) -> tuple[str, str]:
    """A code typed into the sign-up page's "Have a referral code?" box. A valid one is kept in the session exactly
    like `?ref=` would be, so the organization made at the end of sign-up is attached to the referrer."""
    from growth import campaigns
    from growth.models import ReferralCode

    code = campaigns.clean_code(request.POST.get("referral_code", ""))
    if not code:
        return "", ""
    if not ReferralCode.objects.filter(code=code).exists():
        return code, _("That referral code was not found. Check it, or clear the box.")
    seen = dict(request.session.get(campaigns.SESSION_KEY) or {})
    seen["ref"] = code
    request.session[campaigns.SESSION_KEY] = seen
    return code, ""


def _email_optional(request) -> bool:
    """Whether the invitation waiting in the session is for a role that needs no email (caretaker, maintenance)."""
    token = request.session.get(INVITE_TOKEN_KEY)
    invitation = services.get_invitation(token) if token else None
    return bool(invitation and invitation.is_pending and not email_required_for(invitation.role))


@method_decorator(sensitive_post_parameters("password"), name="dispatch")
class RegisterView(View):
    template_name = "accounts/register.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("accounts:home")
        return render(request, self.template_name, {"form": forms.RegisterForm(email_optional=_email_optional(request)),
                                                    "invited": _invited(request)})

    def post(self, request):
        form = forms.RegisterForm(request.POST, email_optional=_email_optional(request))
        typed_code, code_error = _typed_referral_code(request)
        if form.is_valid() and code_error:
            pass  # shown beside the code box; the person fixes or clears it and continues
        elif form.is_valid() and not _ip_allowed(request, "signup", SIGNUP_PER_IP_HOUR):
            form.add_error(None, _too_many_text())
        elif form.is_valid():
            try:
                user = identity.register_user(
                    phone=form.cleaned_data["phone"],
                    full_name=form.cleaned_data["full_name"],
                    email=form.cleaned_data.get("email", ""),
                    password=form.cleaned_data["password"],
                    request=request,
                )
            except ValidationError as exc:
                if hasattr(exc, "error_dict"):
                    for field, errors in exc.message_dict.items():
                        form.add_error(field if field in form.fields else "password", errors)
                else:
                    form.add_error("password", exc)
            else:
                login(request, user, backend="accounts.backends.PhoneOrEmailBackend")
                try:
                    identity.send_phone_verification(user)
                except OTPError as exc:
                    messages.warning(request, str(exc))
                return redirect("accounts:verify_phone")
        return render(request, self.template_name, {"form": form, "invited": _invited(request),
                                                    "typed_code": typed_code, "code_error": code_error})


class VerifyEmailView(VerifiedUserRequiredMixin, View):
    """Confirms the email with a code. Skippable: a mail problem must not stop someone using the app."""

    template_name = "accounts/verify_email.html"

    def get(self, request):
        user = request.user
        if not user.email:
            return redirect("accounts:email")
        if user.email_verified_at:
            return redirect("accounts:home")
        return render(request, self.template_name, {"form": forms.OTPForm()})

    def post(self, request):
        user = request.user
        if "resend" in request.POST:
            try:
                identity.send_email_verification(user)
                messages.success(request, _("A new code is on its way."))
            except OTPError as exc:
                messages.error(request, str(exc))
            return redirect("accounts:verify_email")
        form = forms.OTPForm(request.POST)
        if form.is_valid():
            if identity.verify_email(user, form.cleaned_data["code"], request=request):
                messages.success(request, _("Email verified."))
                return _after_email(request)
            form.add_error("code", _("That code is wrong or has expired."))
        return render(request, self.template_name, {"form": form})


def _after_email(request):
    """Back to the invitation the person was accepting when they had to add an email, else home."""
    token = request.session.get(INVITE_TOKEN_KEY)
    if token:
        return redirect("accounts:accept_invite", token=token)
    return redirect("accounts:home")


class EmailView(VerifiedUserRequiredMixin, View):
    """Add or change the email, then confirm it."""

    template_name = "accounts/email.html"

    def get(self, request):
        return render(request, self.template_name, {"form": forms.EmailForm(initial={"email": request.user.email})})

    def post(self, request):
        form = forms.EmailForm(request.POST)
        if form.is_valid():
            try:
                identity.set_email(request.user, form.cleaned_data["email"], request=request)
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                if request.user.email_verified_at:
                    return _after_email(request)
                try:
                    identity.send_email_verification(request.user)
                except OTPError as exc:
                    messages.warning(request, str(exc))
                return redirect("accounts:verify_email")
        return render(request, self.template_name, {"form": form})


class VerifyPhoneView(LoginRequiredMixin, View):
    template_name = "accounts/verify_phone.html"

    def get(self, request):
        if request.user.phone_verified:
            return redirect("accounts:home")
        return render(request, self.template_name, {"form": forms.OTPForm()})

    def post(self, request):
        if "resend" in request.POST:
            try:
                identity.send_phone_verification(request.user)
                messages.success(request, _("A new code is on its way."))
            except OTPError as exc:
                messages.error(request, str(exc))
            return redirect("accounts:verify_phone")
        form = forms.OTPForm(request.POST)
        if form.is_valid() and identity.verify_phone(request.user, form.cleaned_data["code"], request=request):
            messages.success(request, _("Phone number verified."))
            if request.session.get(INVITE_TOKEN_KEY):
                return redirect("accounts:accept_invite", token=request.session[INVITE_TOKEN_KEY])
            if request.session.get(PORTAL_INVITE_KEY):
                return redirect("portal:accept", token=request.session[PORTAL_INVITE_KEY])
            if request.session.get(OWNER_INVITE_KEY):
                return redirect("owner_portal:accept", token=request.session[OWNER_INVITE_KEY])
            user = request.user
            if user.email and not user.email_verified_at:
                try:
                    identity.send_email_verification(user)
                except OTPError as exc:
                    messages.warning(request, str(exc))
                return redirect("accounts:verify_email")
            return redirect("accounts:home")
        if form.is_valid():
            form.add_error("code", _("That code is wrong or has expired."))
        return render(request, self.template_name, {"form": form})


@method_decorator(sensitive_post_parameters("password"), name="dispatch")
class LoginView(View):
    template_name = "accounts/login.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("accounts:home")
        return render(request, self.template_name, {"form": forms.LoginForm(request)})

    def post(self, request):
        form = forms.LoginForm(request, request.POST)
        if form.is_valid():
            next_url = request.POST.get("next") or request.GET.get("next") or ""
            if mfa.is_enabled(form.user):
                # Not logged in until the second step (D-059).
                mfa.begin_login(request, form.user, next_url)
                return redirect("accounts:login_mfa")
            login(request, form.user)
            audit.record("user.login", actor=form.user, obj=form.user, request=request)
            return _after_login(request, next_url)
        return render(request, self.template_name, {"form": form})


def _after_login(request, next_url):
    if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}, request.is_secure()):
        return redirect(next_url)
    return redirect("accounts:home")


def _check_code(request, user, form):
    """The kind of code accepted, or None with the error added to the form."""
    try:
        kind = mfa.check(user, form.cleaned_data["code"], request=request)
    except mfa.MFAError as exc:
        form.add_error("code", str(exc))
        return None
    if kind is None:
        form.add_error("code", _("That code is wrong. Check the time on your phone and try again."))
    elif kind == "recovery":
        left = mfa.remaining_recovery_codes(user)
        messages.warning(request, _("You used a recovery code. %(n)s left: make new ones from Security.") % {"n": left})
    return kind


@method_decorator(sensitive_post_parameters("code"), name="dispatch")
class LoginMFAView(View):
    """Second step of login, and a fresh check for a session that has not passed one (the admin gate)."""

    template_name = "accounts/login_mfa.html"

    def _user(self, request):
        if request.user.is_authenticated:
            return (request.user, None) if mfa.is_enabled(request.user) else (None, None)
        return mfa.pending_user(request)

    def get(self, request):
        user, _data = self._user(request)
        if user is None:
            return redirect("accounts:home" if request.user.is_authenticated else "accounts:login")
        return render(request, self.template_name, {"form": forms.MFACodeForm(), "next": request.GET.get("next", "")})

    def post(self, request):
        user, data = self._user(request)
        if user is None:
            if not request.user.is_authenticated:
                messages.error(request, _("That took too long. Log in again."))
            return redirect("accounts:home" if request.user.is_authenticated else "accounts:login")
        form = forms.MFACodeForm(request.POST)
        kind = _check_code(request, user, form) if form.is_valid() else None
        if kind is None:
            return render(request, self.template_name, {"form": form, "next": request.POST.get("next", "")})
        if data is not None:
            request.session.pop(mfa.PENDING, None)
            login(request, user, backend=data.get("backend") or None)
            audit.record("user.login", actor=user, obj=user, request=request, changes={"mfa": [None, kind]})
            next_url = data.get("next", "")
        else:
            next_url = request.POST.get("next", "")
        request.session[mfa.VERIFIED] = True
        return _after_login(request, next_url)


class LogoutView(View):
    def post(self, request):
        logout(request)
        return redirect("accounts:login")


class PasswordResetRequestView(View):
    template_name = "accounts/password_reset_request.html"

    def get(self, request):
        return render(request, self.template_name, {"form": forms.PasswordResetRequestForm()})

    def post(self, request):
        form = forms.PasswordResetRequestForm(request.POST)
        if form.is_valid() and not _ip_allowed(request, "reset", RESET_PER_IP_HOUR):
            form.add_error(None, _too_many_text())
        elif form.is_valid():
            identifier = form.cleaned_data["identifier"]
            try:
                identity.request_password_reset(identifier)
            except OTPError as exc:
                messages.error(request, str(exc))
                return render(request, self.template_name, {"form": form})
            request.session[RESET_PHONE_KEY] = identifier
            messages.info(request, _("If we have an account for that, we sent it a code."))
            return redirect("accounts:password_reset_confirm")
        return render(request, self.template_name, {"form": form})


@method_decorator(sensitive_post_parameters("new_password", "code"), name="dispatch")
class PasswordResetConfirmView(View):
    template_name = "accounts/password_reset_confirm.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.session.get(RESET_PHONE_KEY):
            return redirect("accounts:password_reset")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, self.template_name, {"form": forms.PasswordResetConfirmForm()})

    def post(self, request):
        form = forms.PasswordResetConfirmForm(request.POST)
        if form.is_valid():
            try:
                user = identity.reset_password(
                    identifier=request.session[RESET_PHONE_KEY],
                    code=form.cleaned_data["code"],
                    new_password=form.cleaned_data["new_password"],
                    request=request,
                )
            except ValidationError as exc:
                form.add_error("new_password", exc)
            else:
                if user is not None:
                    del request.session[RESET_PHONE_KEY]
                    messages.success(request, _("Password changed. Log in with your new password."))
                    return redirect("accounts:login")
                form.add_error("code", _("That code is wrong or has expired."))
        return render(request, self.template_name, {"form": form})


# ---------------------------------------------------------------------------
# Onboarding, switching, home
# ---------------------------------------------------------------------------


class OnboardingView(VerifiedUserRequiredMixin, View):
    template_name = "accounts/onboarding.html"

    def get(self, request):
        return render(request, self.template_name, {"form": forms.OrganizationForm()})

    def post(self, request):
        form = forms.OrganizationForm(request.POST)
        if form.is_valid():
            if not request.user.email:  # the owner of a workspace needs an email (D-151)
                messages.error(request, _("Add your email address first: the owner of a workspace needs one."))
                return redirect("accounts:email")
            membership = services.create_organization(
                user=request.user, name=form.cleaned_data["name"], org_type=form.cleaned_data["org_type"],
                request=request,
            )
            request.session[SESSION_KEY] = str(membership.organization.public_id)
            messages.success(request, _("Welcome! Your workspace is ready."))
            return redirect("accounts:home")
        return render(request, self.template_name, {"form": form})


class SwitchOrganizationView(LoginRequiredMixin, View):
    def post(self, request):
        wanted = request.POST.get("organization")
        if any(str(m.organization.public_id) == wanted for m in request.user_memberships):
            request.session[SESSION_KEY] = wanted
        return redirect("accounts:home")


class HomeView(OrgMemberRequiredMixin, TemplateView):
    """Each member's home (D-056): work waiting, then figures, then links; all by capability, not role name."""

    template_name = "accounts/home.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:  # a visitor sees the front page, not a login redirect (D-133)
            from growth.views import landing

            return landing(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        m = self.request.membership
        org = m.organization
        if can(m, "properties.manage"):
            steps = setup.checklist(org)
            required = [s for s in steps if not s.optional]
            done = sum(s.done for s in required)
            ctx["checklist"] = steps
            ctx["checklist_done"], ctx["checklist_total"] = done, len(required)
            ctx["show_checklist"] = done < len(required)
            ctx["help_url"] = reverse("support:help_topic", args=["getting-started"])
        ctx["show_staff"] = can(m, "staff.view")
        ctx["show_roles"] = can(m, "roles.manage")
        ctx["suggest_mfa"] = mfa.suggest(m)
        # The email ask is the banner in base.html (email_notice), shown on every page, so not repeated here.
        hour = timezone.localtime().hour
        ctx["greeting"] = (_("Good morning") if hour < 12 else _("Good afternoon") if hour < 17
                           else _("Good evening"))
        # The figures are kept for a few minutes and dropped when this organization's data changes (core/reportcache.py).
        ctx["home"] = home = reportcache.cached(m, "home", (), lambda: reports_home.home(m))
        ctx["board"] = home.board
        return ctx


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------


class StaffListView(CapabilityRequiredMixin, TemplateView):
    template_name = "accounts/staff_list.html"
    required_capability = "staff.view"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        org = self.request.organization
        ctx["members"] = (
            Membership.objects.for_org(org).select_related("user", "role").order_by("user__full_name")
        )
        ctx["invitations"] = [
            i for i in Invitation.objects.filter(organization=org, accepted_at__isnull=True,
                                                 revoked_at__isnull=True).select_related("role")
            if i.is_pending
        ]
        ctx["can_manage"] = can(self.request.membership, "staff.manage")
        ctx["can_api_keys"] = can(self.request.membership, "organization.manage")
        ctx["mfa_users"] = set(TOTPDevice.objects.filter(user__memberships__organization=org,
                                                         confirmed_at__isnull=False).values_list("user_id", flat=True))
        ctx["can_require_mfa"] = ctx["can_api_keys"]
        ctx["self_mfa"] = mfa.is_enabled(self.request.user)
        return ctx


class RequireMFAView(CapabilityRequiredMixin, View):
    """Switches two-step login for all staff on or off (D-107)."""

    required_capability = "organization.manage"

    def post(self, request):
        on = request.POST.get("on") == "1"
        try:
            services.set_require_mfa(request.membership, on, request=request)
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
        else:
            messages.success(request, _("Every member now needs two-step login.") if on
                             else _("Two-step login is now each member's choice."))
        return redirect("accounts:staff")


# Other pages that change how the organization works, so an owner finds them all from one place (D-123).
# (label, what it changes, capability, url name)
SETTINGS_PAGES = (
    (_lazy("Messages and reminders"), _lazy("Which reminders go out, when, quiet hours and reply-to email"),
     "organization.manage", "notifications:settings"),
    (_lazy("Message wording"), _lazy("The text of SMS, WhatsApp and email messages"),
     "templates.manage", "notifications:templates"),
    (_lazy("Tenancy letters"), _lazy("What tenancy and payment record letters state"),
     "organization.manage", "letters:settings"),
    (_lazy("Charge types"), _lazy("Rent, water, garbage and other charges"), "charges.manage", "billing:charge_types"),
    (_lazy("M-Pesa and payment accounts"), _lazy("Paybill, till and bank accounts tenants pay into"),
     "mpesa.settings", "mpesa:settings"),
    (_lazy("Expense categories"), _lazy("How expenses are grouped in reports"), "expenses.view",
     "expenses:categories"),
    (_lazy("Repair targets"), _lazy("How fast repairs should be done, by priority"), "organization.manage",
     "maintenance:targets"),
    (_lazy("Tax residence"), _lazy("Which rate the rental income tax estimate uses"), "organization.manage",
     "reports:tax_residence"),
    (_lazy("Assistant knowledge"), _lazy("What the assistant knows about your business"), "organization.manage",
     "ai_assistant:knowledge"),
    (_lazy("Staff and two-step login"), _lazy("Who works here, their property access and login rules"),
     "staff.view", "accounts:staff"),
    (_lazy("Branches"), _lazy("Your offices, which properties each runs and which staff see them"),
     "organization.manage", "accounts:branches"),
    (_lazy("Roles"), _lazy("What each role can do"), "roles.manage", "accounts:roles"),
    (_lazy("Subscription and SMS sender name"), _lazy("Your plan, payments and the name SMS come from"),
     "subscription.view", "subscriptions:page"),
)


class OrganizationSettingsView(CapabilityRequiredMixin, View):
    """The organization's own details and billing habits, plus links to every other settings page (D-123)."""

    template_name = "accounts/organization.html"
    required_capability = "organization.manage"

    def render(self, request, form, status=200):
        m = request.membership
        pages = [{"label": label, "hint": hint, "url": reverse(name)}
                 for label, hint, capability, name in SETTINGS_PAGES if can(m, capability)]
        return render(request, self.template_name, {"form": form, "pages": pages,
                                                    "can_export": can(m, "organization.export_data"),
                                                    "exports": data_export.recent(request.organization, request.user)},
                      status=status)

    def get(self, request):
        org = request.organization
        return self.render(request, forms.OrganizationSettingsForm(
            initial={f: getattr(org, f) for f in services.ORGANIZATION_FIELDS}, organization=org))

    def post(self, request):
        form = forms.OrganizationSettingsForm(request.POST, request.FILES, organization=request.organization)
        if not form.is_valid():
            return self.render(request, form, status=400)
        values = dict(form.cleaned_data)
        logo, remove_logo = values.pop("logo"), values.pop("remove_logo")
        stamp, remove_stamp = values.pop("stamp"), values.pop("remove_stamp")
        try:
            changed = services.update_organization(request.membership, request=request, **values)
            changed = services.set_logo(request.membership, upload=logo, remove=remove_logo, request=request) or changed
            changed = services.set_stamp(request.membership, upload=stamp, remove=remove_stamp, request=request) or changed
        except ValidationError as exc:
            form.add_error(None, _error_text(exc))
            return self.render(request, form, status=400)
        messages.success(request, _("Organization details saved.") if changed else _("Nothing changed."))
        return redirect("accounts:organization")


class OrganizationExportView(CapabilityRequiredMixin, View):
    """Asks for all the organization's data as one ZIP (D-177); the managers are told by SMS (accounts.alerts).

    The run_data_exports job makes it and texts a link to the Settings page, where only the one who asked may
    download it until it expires.
    """

    required_capability = "organization.export_data"

    def post(self, request):
        _export, queued = data_export.request_export(request.organization, request.user, request)
        messages.success(request, _("We are putting your data together. We will text you when it is ready to download.")
                         if queued else _("Your data is already being put together. We will text you when it is ready."))
        return redirect(f"{reverse('accounts:organization')}#your-data")


class OrganizationExportDownloadView(CapabilityRequiredMixin, View):
    required_capability = "organization.export_data"

    def get(self, request, public_id):
        export = get_object_or_404(DataExport, public_id=public_id, organization=request.organization,
                                   for_console=False)
        return data_export.serve(export, request)


class BranchListView(CapabilityRequiredMixin, View):
    """An agency's offices (D-127): staff given a branch see every property in it."""

    template_name = "accounts/branch_list.html"
    required_capability = "organization.manage"

    def render(self, request, form, status=200):
        return render(request, self.template_name, {"branches": branches.listing(request.organization),
                                                    "form": form}, status=status)

    def get(self, request):
        return self.render(request, forms.BranchForm())

    def post(self, request):
        form = forms.BranchForm(request.POST)
        if form.is_valid():
            try:
                branches.create_branch(request.membership, request=request, **form.cleaned_data)
            except ValidationError as exc:
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Branch added. Choose it on its properties, then give staff access."))
                return redirect("accounts:branches")
        return self.render(request, form, status=400)


class BranchEditView(CapabilityRequiredMixin, View):
    template_name = "accounts/branch_form.html"
    required_capability = "organization.manage"

    def _branch(self, request, public_id) -> Branch:
        return get_object_or_404(Branch.all_objects.for_org(request.organization), public_id=public_id)

    def render(self, request, branch, form, status=200):
        org = request.organization
        return render(request, self.template_name, {
            "branch": branch, "form": form,
            "properties": Property.all_objects.for_org(org).filter(branch=branch).order_by("archived_at", "name"),
            "members": (Membership.objects.for_org(org).filter(branches=branch).select_related("user", "role")
                        .order_by("user__full_name")),
        }, status=status)

    def get(self, request, public_id):
        branch = self._branch(request, public_id)
        return self.render(request, branch, forms.BranchForm(instance=branch))

    def post(self, request, public_id):
        branch = self._branch(request, public_id)
        action = request.POST.get("action", "save")
        if action in ("archive", "restore"):
            try:
                if action == "archive":
                    branches.archive_branch(request.membership, branch, request=request)
                    messages.success(request, _("Branch archived."))
                else:
                    branches.restore_branch(request.membership, branch, request=request)
                    messages.success(request, _("Branch restored."))
            except ValidationError as exc:
                messages.error(request, _error_text(exc))
            return redirect("accounts:branch", public_id=branch.public_id)
        form = forms.BranchForm(request.POST, instance=branch)
        if form.is_valid():
            try:
                branches.update_branch(request.membership, branch, request=request, **form.cleaned_data)
            except ValidationError as exc:
                branch.refresh_from_db()
                _apply_errors(form, exc)
            else:
                messages.success(request, _("Branch saved."))
                return redirect("accounts:branches")
        return self.render(request, branch, form, status=400)


class InviteStaffView(CapabilityRequiredMixin, View):
    template_name = "accounts/invite.html"
    required_capability = "staff.manage"

    def get(self, request):
        return render(request, self.template_name, {"form": forms.InviteForm(request.membership)})

    def post(self, request):
        form = forms.InviteForm(request.membership, request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                invitation, token = services.invite_staff(
                    request.membership, phone=d["phone"], role=d["role"], full_name=d["full_name"],
                    email=d["email"], all_properties=d["all_properties"], properties=d["properties"],
                    branches=d.get("branches") or (), request=request,
                )
            except (ValidationError, PermissionDenied) as exc:
                form.add_error(None, _error_text(exc) if isinstance(exc, ValidationError) else str(exc))
            else:
                url = request.build_absolute_uri(reverse("accounts:accept_invite", args=[token]))
                services.send_invitation(invitation, url)
                messages.success(request, _("Invitation sent to %(phone)s.") % {"phone": invitation.phone})
                return redirect("accounts:staff")
        return render(request, self.template_name, {"form": form})


class RevokeInvitationView(CapabilityRequiredMixin, View):
    required_capability = "staff.manage"

    def post(self, request, public_id):
        invitation = get_object_or_404(Invitation, public_id=public_id, organization=request.organization)
        services.revoke_invitation(request.membership, invitation, request=request)
        messages.success(request, _("Invitation revoked."))
        return redirect("accounts:staff")


class AcceptInvitationView(View):
    """Open to anyone with the link; they must sign up or log in with the invited phone."""

    template_name = "accounts/accept_invite.html"

    def get(self, request, token):
        invitation = services.get_invitation(token)
        if invitation is None or not invitation.is_pending:
            return render(request, self.template_name, {"invalid": True}, status=404)
        request.session[INVITE_TOKEN_KEY] = token
        return render(request, self.template_name, {"invitation": invitation})

    def post(self, request, token):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('accounts:login')}?next={request.path}")
        if not request.user.phone_verified:
            return redirect("accounts:verify_phone")
        try:
            membership = services.accept_invitation(token, request.user, request=request)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _error_text(exc) if isinstance(exc, ValidationError) else str(exc))
            if not request.user.email:
                return redirect("accounts:email")
            return redirect("accounts:accept_invite", token=token)
        request.session.pop(INVITE_TOKEN_KEY, None)
        request.session[SESSION_KEY] = str(membership.organization.public_id)
        messages.success(request, _("You joined %(org)s.") % {"org": membership.organization.name})
        return redirect("accounts:home")


class MemberDetailView(CapabilityRequiredMixin, View):
    template_name = "accounts/member_detail.html"
    required_capability = "staff.manage"

    def get_member(self, request, public_id):
        return get_object_or_404(
            Membership.objects.for_org(request.organization).select_related("user", "role"), public_id=public_id
        )

    def context(self, request, member, **forms_):
        actor = request.membership
        overrides = {o.capability.codename: o.granted for o in member.overrides.select_related("capability")}
        member_caps = effective_capabilities(member)
        return {
            "member": member,
            "role_form": forms_.get("role_form") or forms.MemberRoleForm(actor, initial={"role": member.role}),
            "scope_form": forms_.get("scope_form") or forms.MemberScopeForm(
                actor, initial={"all_properties": member.all_properties,
                                "properties": list(member.property_access.values_list("property_id", flat=True)),
                                "branches": list(member.branches.values_list("pk", flat=True))}),
            "member_branches": list(member.branches.filter(archived_at__isnull=True)),
            "groups": forms.grouped_capabilities(),
            "overrides": overrides,
            "member_caps": member_caps,
            "is_self": member.pk == actor.pk,
        }

    def get(self, request, public_id):
        member = self.get_member(request, public_id)
        return render(request, self.template_name, self.context(request, member))

    def post(self, request, public_id):
        member = self.get_member(request, public_id)
        actor = request.membership
        action = request.POST.get("action")
        extra = {}
        try:
            if action == "role":
                form = forms.MemberRoleForm(actor, request.POST)
                extra["role_form"] = form
                if form.is_valid():
                    services.change_member_role(actor, member, form.cleaned_data["role"], request=request)
                    messages.success(request, _("Role changed."))
            elif action == "scope":
                form = forms.MemberScopeForm(actor, request.POST)
                extra["scope_form"] = form
                if form.is_valid():
                    services.set_property_scope(actor, member, all_properties=form.cleaned_data["all_properties"],
                                                properties=form.cleaned_data["properties"],
                                                branches=form.cleaned_data.get("branches") or (), request=request)
                    messages.success(request, _("Property access saved."))
            elif action == "override":
                form = forms.OverrideForm(request.POST)
                if form.is_valid():
                    services.set_override(actor, member, form.cleaned_data["capability"], form.granted(),
                                          request=request)
                    messages.success(request, _("Permission updated."))
            elif action in ("suspend", "reactivate"):
                services.set_member_active(actor, member, action == "reactivate", request=request)
                messages.success(request, _("Member updated."))
            elif action == "remove":
                services.remove_member(actor, member, request=request)
                messages.success(request, _("Member removed."))
                return redirect("accounts:staff")
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
        except PermissionDenied as exc:
            messages.error(request, str(exc) or _("You are not allowed to do that."))
        if extra and any(f.errors for f in extra.values()):
            return render(request, self.template_name, self.context(request, member, **extra))
        return redirect("accounts:member", public_id=member.public_id)


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


class RoleListView(CapabilityRequiredMixin, TemplateView):
    template_name = "accounts/role_list.html"
    required_capability = "roles.manage"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["roles"] = Role.objects.for_org(self.request.organization).annotate(
            member_count=Count("memberships", filter=Q(memberships__archived_at__isnull=True)),
            capability_count=Count("capabilities", filter=Q(capabilities__is_active=True), distinct=True),
        )
        return ctx


class RoleEditView(CapabilityRequiredMixin, View):
    """Create (no public_id) or edit a role."""

    template_name = "accounts/role_form.html"
    required_capability = "roles.manage"

    def get_role(self, request, public_id):
        if public_id is None:
            return None
        return get_object_or_404(Role.objects.for_org(request.organization), public_id=public_id)

    def render_form(self, request, role, form):
        actor_caps = effective_capabilities(request.membership)
        return render(request, self.template_name, {
            "role": role,
            "form": form,
            "groups": forms.grouped_capabilities(),
            "selected": set(form["capabilities"].value() or []),
            "actor_caps": actor_caps,
            "locked": OWNER_CRITICAL if role and role.is_owner_role else set(),
            "clone_form": forms.CloneRoleForm(initial={"name": f"{role.name} (copy)"}) if role else None,
        })

    def get(self, request, public_id=None):
        role = self.get_role(request, public_id)
        initial = {}
        if role:
            initial = {"name": role.name, "description": role.description,
                       "capabilities": sorted(services.role_codenames(role))}
        return self.render_form(request, role, forms.RoleForm(initial=initial))

    def post(self, request, public_id=None):
        role = self.get_role(request, public_id)
        form = forms.RoleForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                if role is None:
                    role = services.create_role(request.membership, name=d["name"], description=d["description"],
                                                capabilities=d["capabilities"], request=request)
                else:
                    services.update_role(request.membership, role, name=d["name"], description=d["description"],
                                         capabilities=d["capabilities"], request=request)
            except ValidationError as exc:
                form.add_error(None, _error_text(exc))
            except PermissionDenied as exc:
                form.add_error(None, str(exc) or _("You are not allowed to do that."))
            else:
                messages.success(request, _("Role saved."))
                return redirect("accounts:roles")
        return self.render_form(request, role, form)


class RoleCloneView(CapabilityRequiredMixin, View):
    required_capability = "roles.manage"

    def post(self, request, public_id):
        role = get_object_or_404(Role.objects.for_org(request.organization), public_id=public_id)
        form = forms.CloneRoleForm(request.POST)
        if form.is_valid():
            try:
                new = services.clone_role(request.membership, role, name=form.cleaned_data["name"], request=request)
            except (ValidationError, PermissionDenied) as exc:
                messages.error(request, _error_text(exc) if isinstance(exc, ValidationError) else str(exc))
            else:
                messages.success(request, _("Role copied."))
                return redirect("accounts:role_edit", public_id=new.public_id)
        return redirect("accounts:role_edit", public_id=role.public_id)


class RoleArchiveView(CapabilityRequiredMixin, View):
    required_capability = "roles.manage"

    def post(self, request, public_id):
        role = get_object_or_404(Role.objects.for_org(request.organization), public_id=public_id)
        try:
            services.archive_role(request.membership, role, request=request)
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
            return redirect("accounts:role_edit", public_id=role.public_id)
        messages.success(request, _("Role removed."))
        return redirect("accounts:roles")


# ---------------------------------------------------------------------------
# Security: two-step login (D-059)
# ---------------------------------------------------------------------------


@method_decorator(sensitive_post_parameters("password", "code"), name="dispatch")
class SecurityView(LoginRequiredMixin, View):
    template_name = "accounts/security.html"

    def _render(self, request, **ctx):
        user = request.user
        ctx.setdefault("enabled", mfa.is_enabled(user))
        if not ctx["enabled"]:
            secret = mfa.pending_secret(user)
            if secret:
                uri = mfa.provisioning_uri(user, secret)
                ctx.update(secret=secret, uri=uri, qr=mfa.qr_svg(uri))
                ctx.setdefault("confirm_form", forms.MFACodeForm())
        else:
            ctx["left"] = mfa.remaining_recovery_codes(user)
            ctx.setdefault("renew_form", forms.MFACodeForm(prefix="renew"))
            ctx.setdefault("disable_form", forms.MFADisableForm(prefix="off"))
        ctx["required"] = user.is_staff
        ctx["required_by"] = mfa.requiring_orgs(user)
        return render(request, self.template_name, ctx)

    def get(self, request):
        return self._render(request)

    def post(self, request):
        user = request.user
        action = request.POST.get("action")
        if action == "start":
            try:
                mfa.start_setup(user)
            except ValidationError as exc:
                messages.error(request, _error_text(exc))
            return redirect("accounts:security")
        if action == "confirm":
            form = forms.MFACodeForm(request.POST)
            if form.is_valid():
                try:
                    codes = mfa.confirm_setup(user, form.cleaned_data["code"], request=request)
                except ValidationError as exc:
                    form.add_error("code", exc)
                else:
                    messages.success(request, _("Two-step login is on."))
                    return self._render(request, enabled=True, codes=codes)
            return self._render(request, confirm_form=form)
        if action == "renew":
            form = forms.MFACodeForm(request.POST, prefix="renew")
            if form.is_valid():
                try:
                    codes = mfa.regenerate_recovery_codes(user, form.cleaned_data["code"], request=request)
                except (ValidationError, mfa.MFAError) as exc:
                    form.add_error("code", _error_text(exc) if isinstance(exc, ValidationError) else str(exc))
                else:
                    messages.success(request, _("New recovery codes made. The old ones no longer work."))
                    return self._render(request, codes=codes)
            return self._render(request, renew_form=form)
        if action == "disable":
            form = forms.MFADisableForm(request.POST, prefix="off")
            if form.is_valid():
                try:
                    mfa.disable(user, password=form.cleaned_data["password"], code=form.cleaned_data["code"],
                                request=request)
                except ValidationError as exc:
                    for field, errors in exc.message_dict.items():
                        form.add_error(field, errors)
                except mfa.MFAError as exc:
                    form.add_error("code", str(exc))
                else:
                    messages.success(request, _("Two-step login is off."))
                    return redirect("accounts:security")
            return self._render(request, disable_form=form)
        return redirect("accounts:security")
