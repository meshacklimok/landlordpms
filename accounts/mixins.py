"""View mixins for organization scoping and capability checks.

Usage::

    class TenantListView(CapabilityRequiredMixin, OrgScopedQuerysetMixin, ListView):
        model = Tenant
        required_capability = "tenants.view"
"""

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from .permissions import can, is_membership_usable


class VerifiedUserRequiredMixin(AccessMixin):
    """Logged in, with a verified phone."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not request.user.phone_verified:
            return redirect("accounts:verify_phone")
        return super().dispatch(request, *args, **kwargs)


class OrgMemberRequiredMixin(VerifiedUserRequiredMixin):
    """Needs an active organization in the session."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.phone_verified:
            if request.membership is None:
                return redirect("accounts:onboarding")
            if not is_membership_usable(request.membership):
                raise PermissionDenied("This organization is not available.")
        return super().dispatch(request, *args, **kwargs)


class CapabilityRequiredMixin(OrgMemberRequiredMixin):
    required_capability: str | None = None

    def dispatch(self, request, *args, **kwargs):
        if (
            self.required_capability
            and request.user.is_authenticated
            and request.user.phone_verified
            and request.membership is not None
            and not can(request.membership, self.required_capability)
        ):
            raise PermissionDenied(self.required_capability)
        return super().dispatch(request, *args, **kwargs)


class OrgScopedQuerysetMixin:
    """Objects come only from the active organization and are looked up by UUID."""

    slug_field = "public_id"
    slug_url_kwarg = "public_id"

    def get_queryset(self):
        return super().get_queryset().for_org(self.request.organization)
