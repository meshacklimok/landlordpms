"""The owner desk screens (D-217): the owner's side in the portal and the office's side on the owner page."""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from properties.models import PropertyOwner

from . import desk, services
from .models import OwnerApproval, OwnerPayoutDetail, OwnerPayoutRequest
from .views import OwnerPortalRequiredMixin, _error_text, _no_store


class OwnerOnlyMixin(OwnerPortalRequiredMixin):
    """Guests the owner invited read the figures; the desk and sharing are the owner's alone."""

    def account(self, request, public_id):
        account = super().account(request, public_id)
        if account.is_delegate:
            raise Http404
        return account



def _flash(request, fn, ok: str):
    try:
        fn()
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, _error_text(exc))
        return False
    messages.success(request, ok)
    return True


# The owner's side ---------------------------------------------------------------------------------------------


class DeskView(OwnerOnlyMixin, View):
    template_name = "owner_portal/desk.html"

    def get(self, request, public_id):
        account = self.account(request, public_id)
        owner = account.owner
        thread = desk.thread(owner)
        desk.mark_read_by_owner(owner)
        return _no_store(render(request, self.template_name, {
            "account": account, "owner": owner, "currency": account.organization.currency,
            "thread": thread, "approvals": desk.approvals(owner)[:30],
            "asked": OwnerApproval.Status.ASKED, "confirmed": desk.confirmed_detail(owner),
            "pending": desk.pending_detail(owner), "open_request": desk.open_request(owner),
            "requests": OwnerPayoutRequest.objects.filter(owner=owner)[:10],
            "methods": OwnerPayoutDetail.Method.choices, "max_length": desk.OwnerMessage.MAX_LENGTH,
        }))


class _OwnerPost(OwnerOnlyMixin, View):
    def back(self, account):
        return redirect("owner_portal:desk", public_id=account.public_id)


class SendView(_OwnerPost):
    def post(self, request, public_id):
        account = self.account(request, public_id)
        _flash(request, lambda: desk.owner_send(account, request.POST.get("body", "")), _("Message sent."))
        return self.back(account)


class AnswerView(_OwnerPost):
    def post(self, request, public_id, approval_id):
        account = self.account(request, public_id)
        approval = get_object_or_404(OwnerApproval, owner=account.owner, public_id=approval_id)
        decision = request.POST.get("decision")
        if decision not in ("approve", "decline"):
            raise Http404
        _flash(request, lambda: desk.answer_approval(account, approval, decision == "approve",
                                                     request.POST.get("note", ""), request=request),
               _("Your answer was sent to the office."))
        return self.back(account)


class DetailView(_OwnerPost):
    def post(self, request, public_id):
        account = self.account(request, public_id)
        p = request.POST
        _flash(request, lambda: desk.submit_detail(
            account, method=p.get("method", ""), account_name=p.get("account_name", ""),
            account_number=p.get("account_number", ""), bank_name=p.get("bank_name", ""), request=request),
            _("Sent. The office will confirm these details with you before they are used."))
        return self.back(account)


class RequestView(_OwnerPost):
    def post(self, request, public_id):
        account = self.account(request, public_id)
        raw = request.POST.get("amount", "").replace(",", "").strip()
        try:
            amount = Decimal(raw) if raw else None
        except InvalidOperation:
            messages.error(request, _("Enter a valid amount."))
            return self.back(account)
        if amount is not None and amount <= 0:
            messages.error(request, _("Enter a valid amount."))
            return self.back(account)
        _flash(request, lambda: desk.request_payout(account, amount, request.POST.get("note", ""), request=request),
               _("The office was asked to pay you."))
        return self.back(account)


class RequestWithdrawView(_OwnerPost):
    def post(self, request, public_id):
        account = self.account(request, public_id)
        _flash(request, lambda: desk.withdraw_request(account, request=request), _("Request withdrawn."))
        return self.back(account)


class GuestsView(OwnerOnlyMixin, View):
    """The people the owner chose to share the portal with: read-only, up to three."""

    template_name = "owner_portal/guests.html"

    def get(self, request, public_id):
        account = self.account(request, public_id)
        return _no_store(render(request, self.template_name, {
            "account": account, "owner": account.owner, "guests": services.guests(account.owner),
            "max_guests": services.MAX_GUESTS}))

    def post(self, request, public_id):
        account = self.account(request, public_id)
        try:
            invitation, token = services.invite_guest(account, request.POST.get("name", ""),
                                                      request.POST.get("phone", ""), request=request)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _error_text(exc))
            return redirect("owner_portal:guests", public_id=account.public_id)
        url = request.build_absolute_uri(reverse("owner_portal:accept", args=[token]))
        if services.send_guest_invitation(invitation, url):
            messages.success(request, _("Invitation sent to %(name)s. It works for 7 days.")
                             % {"name": invitation.delegate_name})
        else:
            messages.warning(request, _("The invitation was made but the SMS failed. Remove it and try again."))
        return redirect("owner_portal:guests", public_id=account.public_id)


class GuestRemoveView(OwnerOnlyMixin, View):
    def post(self, request, public_id):
        account = self.account(request, public_id)
        try:
            services.remove_guest(account, account_id=request.POST.get("account") or None,
                                  invitation_id=request.POST.get("invitation") or None, request=request)
        except (ValidationError, PermissionDenied, ValueError) as exc:
            messages.error(request, _error_text(exc))
        else:
            messages.success(request, _("Removed."))
        return redirect("owner_portal:guests", public_id=account.public_id)


# The office's side --------------------------------------------------------------------------------------------


def _owner(request, public_id) -> PropertyOwner:
    return get_object_or_404(PropertyOwner, organization=request.organization, public_id=public_id)


def _back(owner):
    return redirect("owner_portal:staff_desk", public_id=owner.public_id)


class StaffDeskView(CapabilityRequiredMixin, View):
    required_capability = "owners.invite_portal"
    template_name = "owner_portal/staff_desk.html"

    def get(self, request, public_id):
        owner = _owner(request, public_id)
        try:
            services._check(request.membership, owner)
        except PermissionDenied:
            raise Http404 from None
        thread = desk.thread(owner)
        desk.mark_read_by_staff(owner)
        return render(request, self.template_name, {
            "owner": owner, "currency": request.organization.currency, "thread": thread,
            "approvals": desk.approvals(owner)[:30], "asked": OwnerApproval.Status.ASKED,
            "askable": desk.askable(owner), "confirmed": desk.confirmed_detail(owner),
            "pending": desk.pending_detail(owner), "open_request": desk.open_request(owner),
            "requests": OwnerPayoutRequest.objects.filter(owner=owner)[:10],
            "max_length": desk.OwnerMessage.MAX_LENGTH,
        })


class StaffReplyView(CapabilityRequiredMixin, View):
    required_capability = "owners.invite_portal"

    def post(self, request, public_id):
        owner = _owner(request, public_id)
        _flash(request, lambda: desk.staff_send(request.membership, owner, request.POST.get("body", "")),
               _("Reply sent."))
        return _back(owner)


class StaffAskView(CapabilityRequiredMixin, View):
    required_capability = "owners.invite_portal"

    def post(self, request, public_id):
        owner = _owner(request, public_id)
        kind, _sep, subject = request.POST.get("subject", "").partition(":")
        _flash(request, lambda: desk.ask_approval(request.membership, owner, kind=kind, subject_id=subject,
                                                  note=request.POST.get("note", ""), request=request),
               _("Asked. The owner was sent an SMS."))
        return _back(owner)


class StaffWithdrawView(CapabilityRequiredMixin, View):
    required_capability = "owners.invite_portal"

    def post(self, request, public_id, approval_id):
        owner = _owner(request, public_id)
        approval = get_object_or_404(OwnerApproval, owner=owner, public_id=approval_id)
        _flash(request, lambda: desk.withdraw_approval(request.membership, approval, request=request),
               _("Withdrawn."))
        return _back(owner)


class StaffDetailView(CapabilityRequiredMixin, View):
    required_capability = "owners.invite_portal"

    def post(self, request, public_id, detail_id):
        owner = _owner(request, public_id)
        detail = get_object_or_404(OwnerPayoutDetail, owner=owner, public_id=detail_id)
        confirm = request.POST.get("decision") == "confirm"
        _flash(request, lambda: desk.decide_detail(request.membership, detail, confirm,
                                                   request.POST.get("reason", ""), request=request),
               _("Payout details confirmed.") if confirm else _("Payout details not accepted."))
        return _back(owner)


class StaffRequestView(CapabilityRequiredMixin, View):
    required_capability = "owners.invite_portal"

    def post(self, request, public_id, request_id):
        owner = _owner(request, public_id)
        ask = get_object_or_404(OwnerPayoutRequest, owner=owner, public_id=request_id)
        paid = request.POST.get("decision") == "paid"
        _flash(request, lambda: desk.handle_request(request.membership, ask, paid, request.POST.get("note", ""),
                                                    request=request),
               _("Marked as paid. Record the payment on the owner's account.") if paid else _("Marked as not paid."))
        return _back(owner)
