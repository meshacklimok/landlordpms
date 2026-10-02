"""The tenant portal (D-055) and the staff actions that invite tenants to it.

Portal pages see only `selectors.own_*`: anything outside the user's own leases is a 404.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin, VerifiedUserRequiredMixin
from accounts.views import PORTAL_INVITE_KEY
from leases.models import Lease
from maintenance import forms as maintenance_forms
from maintenance import services as maintenance
from maintenance.views import photo_response
from tenants.models import Tenant
from tenants.services import visible_tenants

from . import selectors, services


def _error_text(exc) -> str:
    if isinstance(exc, ValidationError):
        return " ".join(exc.messages)
    return str(exc)


class PortalRequiredMixin(VerifiedUserRequiredMixin):
    """Logged in with a verified phone and a live tenant account; anyone else gets a 404."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.phone_verified and not selectors.has_access(request.user):
            raise Http404
        return super().dispatch(request, *args, **kwargs)


def _no_store(response):
    response["Cache-Control"] = "private, no-store"
    return response


class HomeView(PortalRequiredMixin, View):
    template_name = "portal/home.html"

    def get(self, request):
        leases = list(selectors.own_leases(request.user))
        return _no_store(render(request, self.template_name, {
            "summaries": [selectors.summary(lease) for lease in leases],
        }))


class LeaseView(PortalRequiredMixin, View):
    template_name = "portal/lease.html"

    def get(self, request, public_id):
        lease = selectors.own_lease(request.user, public_id)
        if lease is None:
            raise Http404
        return _no_store(render(request, self.template_name, {
            "lease": lease,
            "summary": selectors.summary(lease),
            "statement": selectors.statement(lease),
            "payments": selectors.payments(lease),
            "repairs": list(selectors.own_requests(request.user).filter(lease=lease)[:selectors.REQUESTS_SHOWN]),
            "can_report": lease.status == Lease.Status.ACTIVE,
        }))


class RepairCreateView(PortalRequiredMixin, View):
    """Report a problem in the unit of an active lease (D-068 item 7)."""

    template_name = "portal/repair_form.html"

    def load(self, request, public_id) -> Lease:
        lease = selectors.own_lease(request.user, public_id)
        if lease is None or lease.status != Lease.Status.ACTIVE:
            raise Http404
        return lease

    def get(self, request, public_id):
        lease = self.load(request, public_id)
        return _no_store(render(request, self.template_name,
                                {"lease": lease, "form": maintenance_forms.PortalRequestForm()}))

    def post(self, request, public_id):
        lease = self.load(request, public_id)
        form = maintenance_forms.PortalRequestForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                req = maintenance.portal_report(request.user, lease, request=request, **form.cleaned_data)
            except ValidationError as exc:
                if hasattr(exc, "error_dict"):
                    for name, errors in exc.error_dict.items():
                        form.add_error(name if name in form.fields else None, errors)
                else:
                    form.add_error(None, exc)
            except PermissionDenied:
                raise Http404 from None
            else:
                messages.success(request, _("Thank you. We have your report %(n)s.") % {"n": req.number})
                return redirect("portal:repair", public_id=req.public_id)
        if request.FILES:
            form.add_error(None, _("Choose the photos again."))
        return _no_store(render(request, self.template_name, {"lease": lease, "form": form}))


class RepairView(PortalRequiredMixin, View):
    template_name = "portal/repair.html"

    def get(self, request, public_id):
        req = selectors.own_request(request.user, public_id)
        if req is None:
            raise Http404
        return _no_store(render(request, self.template_name, {
            "req": req, "updates": selectors.tenant_updates(req), "photos": selectors.tenant_photos(req, request.user),
            "can_comment": req.status not in (req.Status.CLOSED, req.Status.CANCELLED),
        }))

    def post(self, request, public_id):
        req = selectors.own_request(request.user, public_id)
        if req is None:
            raise Http404
        try:
            maintenance.portal_comment(request.user, req, text=request.POST.get("text", ""), request=request)
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
        except PermissionDenied:
            raise Http404 from None
        else:
            messages.success(request, _("Comment sent."))
        return redirect("portal:repair", public_id=req.public_id)


class RepairPhotoView(PortalRequiredMixin, View):
    def get(self, request, public_id):
        photo = selectors.own_photo(request.user, public_id)
        if photo is None:
            raise Http404
        return photo_response(photo)


class ReceiptView(PortalRequiredMixin, View):
    def get(self, request, public_id):
        payment = selectors.own_payment(request.user, public_id)
        receipt = getattr(payment, "receipt", None) if payment else None
        if receipt is None or not receipt.pdf:
            raise Http404
        return _no_store(FileResponse(receipt.pdf.open("rb"), content_type="application/pdf",
                                      as_attachment=request.GET.get("download") == "1",
                                      filename=f"{receipt.number}.pdf"))


class AcceptView(View):
    """Open to anyone with the link; claiming it needs a login with the invited, verified phone."""

    template_name = "portal/accept.html"

    def _invitation(self, token):
        invitation = services.get_invitation(token)
        return invitation if invitation is not None and invitation.is_pending else None

    def get(self, request, token):
        invitation = self._invitation(token)
        if invitation is None:
            return render(request, self.template_name, {"invalid": True}, status=404)
        request.session[PORTAL_INVITE_KEY] = token
        return render(request, self.template_name, {"invitation": invitation})

    def post(self, request, token):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('accounts:login')}?next={request.path}")
        if not request.user.phone_verified:
            return redirect("accounts:verify_phone")
        try:
            account = services.accept_invitation(token, request.user, request=request)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _error_text(exc))
            return redirect("portal:accept", token=token)
        request.session.pop(PORTAL_INVITE_KEY, None)
        messages.success(request, _("Welcome. Here is your account with %(org)s.")
                         % {"org": account.organization.name})
        return redirect("portal:home")


# ---------------------------------------------------------------------------
# Staff: on the tenant page
# ---------------------------------------------------------------------------


def _staff_tenant(request, public_id) -> Tenant:
    return get_object_or_404(visible_tenants(request.membership, Tenant.all_objects.all()), public_id=public_id)


class InviteView(CapabilityRequiredMixin, View):
    required_capability = "tenants.invite_portal"

    def post(self, request, public_id):
        tenant = _staff_tenant(request, public_id)
        try:
            invitation, token = services.invite_tenant(request.membership, tenant, request=request)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _error_text(exc))
            return redirect("tenants:detail", public_id=tenant.public_id)
        if not services.send_invitation(invitation, request.build_absolute_uri(reverse("portal:accept", args=[token]))):
            messages.warning(request, _("The invitation was made but the SMS could not be sent. Try again."))
        else:
            messages.success(request, _("Invitation sent to %(phone)s. It works for 7 days.")
                             % {"phone": invitation.phone})
        return redirect("tenants:detail", public_id=tenant.public_id)


class RevokeInvitationView(CapabilityRequiredMixin, View):
    required_capability = "tenants.invite_portal"

    def post(self, request, public_id):
        tenant = _staff_tenant(request, public_id)
        invitation = services.pending_invitation(tenant)
        if invitation is not None:
            services.revoke_invitation(request.membership, invitation, request=request)
            messages.success(request, _("Invitation cancelled."))
        return redirect("tenants:detail", public_id=tenant.public_id)


class RevokeAccountView(CapabilityRequiredMixin, View):
    required_capability = "tenants.invite_portal"

    def post(self, request, public_id):
        tenant = _staff_tenant(request, public_id)
        account = services.live_account(tenant)
        if account is not None:
            services.revoke_account(request.membership, account, request=request)
            messages.success(request, _("Portal access removed."))
        return redirect("tenants:detail", public_id=tenant.public_id)


def tenant_card(tenant: Tenant) -> dict:
    """What the tenant page shows about the portal."""
    return {
        "account": services.live_account(tenant),
        "invitation": services.pending_invitation(tenant),
        "has_leases": services.has_leases(tenant),
    }
