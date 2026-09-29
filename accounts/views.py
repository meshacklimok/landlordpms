"""Thin views: forms in, services do the work (D-009)."""

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.debug import sensitive_post_parameters
from django.views.generic import TemplateView

from audit import services as audit
from reports import home as reports_home

from . import forms, identity, mfa, services, setup
from .capabilities import OWNER_CRITICAL
from .middleware import SESSION_KEY
from .mixins import CapabilityRequiredMixin, OrgMemberRequiredMixin, VerifiedUserRequiredMixin
from .models import Invitation, Membership, Role
from .otp import OTPError
from .permissions import can, effective_capabilities

RESET_PHONE_KEY = "reset_phone"
INVITE_TOKEN_KEY = "invite_token"
PORTAL_INVITE_KEY = "portal_invite_token"  # set by portal.views.AcceptView


def _error_text(exc: ValidationError) -> str:
    return " ".join(exc.messages)


# ---------------------------------------------------------------------------
# Sign-up, verification, login, password reset
# ---------------------------------------------------------------------------


@method_decorator(sensitive_post_parameters("password"), name="dispatch")
class RegisterView(View):
    template_name = "accounts/register.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("accounts:home")
        return render(request, self.template_name, {"form": forms.RegisterForm()})

    def post(self, request):
        form = forms.RegisterForm(request.POST)
        if form.is_valid():
            try:
                user = identity.register_user(
                    phone=form.cleaned_data["phone"],
                    full_name=form.cleaned_data["full_name"],
                    email=form.cleaned_data["email"],
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
        if form.is_valid():
            phone = form.cleaned_data["phone"]
            try:
                identity.request_password_reset(phone)
            except OTPError as exc:
                messages.error(request, str(exc))
                return render(request, self.template_name, {"form": form})
            request.session[RESET_PHONE_KEY] = phone
            messages.info(request, _("If an account uses that number, we sent it a code."))
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
                    phone=request.session[RESET_PHONE_KEY],
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
        ctx["home"] = home = reports_home.home(m)
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
        return ctx


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
                    request=request,
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
                                "properties": list(member.property_access.values_list("property_id", flat=True))}),
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
                                                properties=form.cleaned_data["properties"], request=request)
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
