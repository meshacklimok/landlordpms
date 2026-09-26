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
from properties.models import Property, Unit
from tenants.models import Tenant

from . import forms, identity, services
from .capabilities import OWNER_CRITICAL
from .middleware import SESSION_KEY
from .mixins import CapabilityRequiredMixin, OrgMemberRequiredMixin, VerifiedUserRequiredMixin
from .models import Invitation, Membership, Role
from .otp import OTPError
from .permissions import can, effective_capabilities

RESET_PHONE_KEY = "reset_phone"
INVITE_TOKEN_KEY = "invite_token"


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
            login(request, form.user)
            audit.record("user.login", actor=form.user, obj=form.user, request=request)
            next_url = request.POST.get("next") or request.GET.get("next")
            if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}, request.is_secure()):
                return redirect(next_url)
            return redirect("accounts:home")
        return render(request, self.template_name, {"form": form})


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
    """Role-based home shell: cards appear by capability, not role name."""

    template_name = "accounts/home.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        m = self.request.membership
        org = m.organization
        has_property = Property.objects.for_org(org).exists()
        has_unit = Unit.objects.for_org(org).exists()
        # Derived from data, no table (doc 11 §16). Later steps light up in Phase 2–4.
        ctx["checklist"] = [
            (_("Create your workspace"), True),
            (_("Add a property"), has_property),
            (_("Add units"), has_unit),
            (_("Add tenants"), Tenant.objects.for_org(org).exists()),
            (_("Create leases and set rent"), False),
            (_("Add a payment method"), False),
        ]
        ctx["show_checklist"] = can(m, "properties.manage")
        ctx["show_staff"] = can(m, "staff.view")
        ctx["show_roles"] = can(m, "roles.manage")
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
