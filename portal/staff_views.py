"""The office's side of the tenant portal: replying to tenants and renewal offers (D-131)."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can
from leases import services as lease_services
from leases.models import Lease

from . import conversation, forms, offers
from .models import RenewalOffer


def _error_text(exc) -> str:
    return " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


def _lease(request, public_id) -> Lease:
    qs = lease_services.visible_leases(request.membership, Lease.all_objects.all())
    return get_object_or_404(qs.select_related("unit__property", "organization")
                             .prefetch_related("lease_tenants__tenant"), public_id=public_id)


def _offer(request, public_id) -> RenewalOffer:
    qs = RenewalOffer.objects.for_org(request.membership.organization).select_related("lease")
    offer = get_object_or_404(qs, public_id=public_id)
    _lease(request, offer.lease.public_id)  # the lease must be one this member can see
    return offer


class InboxView(CapabilityRequiredMixin, View):
    """Leases where a tenant has written, the ones waiting for a reply first."""

    template_name = "portal/staff_inbox.html"
    required_capability = "messages.view"

    def get(self, request):
        return render(request, self.template_name, {"threads": conversation.staff_threads(request.membership)})


class ThreadView(CapabilityRequiredMixin, View):
    template_name = "portal/staff_thread.html"
    required_capability = "messages.view"

    def _render(self, request, lease, form, status=200):
        return render(request, self.template_name, {
            "lease": lease, "thread": conversation.thread(lease), "form": form,
            "can_reply": can(request.membership, "messages.send", lease.unit.property),
        }, status=status)

    def get(self, request, public_id):
        lease = _lease(request, public_id)
        response = self._render(request, lease, forms.MessageForm())
        conversation.mark_read_by_staff(lease)
        return response

    def post(self, request, public_id):
        lease = _lease(request, public_id)
        form = forms.MessageForm(request.POST)
        if form.is_valid():
            try:
                conversation.staff_reply(request.membership, lease, form.cleaned_data["body"])
            except ValidationError as exc:
                form.add_error(None, exc)
            except PermissionDenied:
                form.add_error(None, _("You cannot reply on this lease."))
            else:
                messages.success(request, _("Reply sent. The tenant is told by SMS."))
                return redirect("conversations:thread", public_id=lease.public_id)
        return self._render(request, lease, form, status=400)


class OfferCreateView(CapabilityRequiredMixin, View):
    required_capability = "leases.draft"

    def post(self, request, public_id):
        lease = _lease(request, public_id)
        form = forms.OfferForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            try:
                offers.make_offer(request.membership, lease, start_date=data["start_date"], end_date=data["end_date"],
                                  rent=data["rent"], note=data["note"], expires_on=data["expires_on"], request=request)
            except ValidationError as exc:
                messages.error(request, _error_text(exc))
            except PermissionDenied:
                messages.error(request, _("You cannot make an offer on this lease."))
            else:
                messages.success(request, _("Renewal offered. The tenant is told by SMS to open the portal."))
        else:
            messages.error(request, _("Check the offer: ") + " ".join(
                f"{form.fields[name].label}: {' '.join(errors)}" for name, errors in form.errors.items()))
        return redirect("leases:detail", public_id=lease.public_id)


class OfferWithdrawView(CapabilityRequiredMixin, View):
    required_capability = "leases.draft"

    def post(self, request, public_id):
        offer = _offer(request, public_id)
        try:
            offers.withdraw(request.membership, offer, request=request)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _error_text(exc))
        else:
            messages.success(request, _("Offer withdrawn."))
        return redirect("leases:detail", public_id=offer.lease.public_id)


class OfferDraftView(CapabilityRequiredMixin, View):
    """Starts the renewal draft from an accepted offer."""

    required_capability = "leases.draft"

    def post(self, request, public_id):
        offer = _offer(request, public_id)
        try:
            draft = offers.start_draft(request.membership, offer, request=request)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _error_text(exc))
            return redirect("leases:detail", public_id=offer.lease.public_id)
        messages.success(request, _("Renewal draft started. Check it, then activate it."))
        return redirect("leases:detail", public_id=draft.public_id)
