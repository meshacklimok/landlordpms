"""The owner portal (D-128) and the staff actions that invite owners to it.

Portal pages see only `selectors.own_*` and `live_accounts`: anything else is a 404.
"""

import csv
import datetime

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin, VerifiedUserRequiredMixin
from accounts.views import OWNER_INVITE_KEY
from leases.services import with_occupancy
from properties.models import PropertyOwner, Unit
from reports import owners, statements
from reports.metrics import add_months
from reports.pdf import render_statement

from . import selectors, services

PICKER_MONTHS = 24


def _error_text(exc) -> str:
    if isinstance(exc, ValidationError):
        return " ".join(exc.messages)
    return str(exc)


def _no_store(response):
    response["Cache-Control"] = "private, no-store"
    return response


class OwnerPortalRequiredMixin(VerifiedUserRequiredMixin):
    """Logged in with a verified phone and a live owner account; anyone else gets a 404."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.phone_verified and not selectors.has_access(request.user):
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def account(self, request, public_id):
        account = selectors.own_account(request.user, public_id)
        if account is None:
            raise Http404
        return account


class HomeView(OwnerPortalRequiredMixin, View):
    """One card per owner record: last month's figures and links to the rest."""

    template_name = "owner_portal/home.html"

    def get(self, request):
        cards = []
        for account in selectors.live_accounts(request.user):
            st = statements.for_owner(account.owner, statements.default_month())
            cards.append({"account": account, "st": st, "properties": selectors.properties(account).count()})
        if len(cards) == 1 and request.GET.get("all") is None:
            return redirect("owner_portal:account", public_id=cards[0]["account"].public_id)
        return _no_store(render(request, self.template_name, {"cards": cards}))


class AccountView(OwnerPortalRequiredMixin, View):
    """The last 12 months, payments to the owner, statements sent and the properties."""

    template_name = "owner_portal/account.html"

    def get(self, request, public_id):
        account = self.account(request, public_id)
        props = []
        for prop in selectors.properties(account):
            units = list(with_occupancy(Unit.objects.filter(property=prop)))
            props.append({"property": prop, "units": len(units),
                          "occupied": sum(1 for u in units if u.is_occupied)})
        acc = owners.owner_account(account.owner)
        currency = account.organization.currency
        return _no_store(render(request, self.template_name, {
            "account": account, "owner": account.owner, "org": account.organization,
            "acc": acc, "currency": currency, "specs": owners.account_specs(acc, currency),
            "sends": selectors.sends(account), "props": props,
            "several": selectors.live_accounts(request.user).count() > 1,
        }))


PAYMENTS_SHOWN = 300


class PaymentsView(OwnerPortalRequiredMixin, View):
    """Rent collected on the owner's properties, by year, with a CSV download. Tenant names are not shown."""

    template_name = "owner_portal/payments.html"

    def get(self, request, public_id):
        account = self.account(request, public_id)
        years = selectors.payment_years(account)
        try:
            year = int(request.GET.get("year", ""))
        except ValueError:
            year = None
        if year not in years:
            year = None
        query = request.GET.get("q", "").strip()[:60]
        found = selectors.payments(account, year, query)
        if request.GET.get("download") == "csv":
            response = HttpResponse(content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="payments{f"-{year}" if year else ""}.csv"'
            writer = csv.writer(response)
            writer.writerow([_("Date"), _("Property"), _("Unit"), _("Method"), _("Reference"), _("Amount")])
            for payment in found:
                unit = payment.lease.unit
                writer.writerow([payment.paid_at.isoformat(), unit.property.name, unit.code,
                                 payment.get_method_display(), payment.reference, payment.amount])
            return _no_store(response)
        total = found.aggregate(total=Sum("amount"))["total"] or 0
        return _no_store(render(request, self.template_name, {
            "account": account, "owner": account.owner, "currency": account.organization.currency,
            "payments": list(found[:PAYMENTS_SHOWN]), "total": total, "years": years, "year": year, "query": query,
            "more": found.count() > PAYMENTS_SHOWN,
        }))


def _month(request) -> datetime.date:
    """`?month=YYYY-MM`, last month when missing, wrong or not over yet."""
    default = statements.default_month()
    try:
        month = datetime.datetime.strptime(request.GET.get("month", ""), "%Y-%m").date()
    except ValueError:
        return default
    return month if month <= default else default


class StatementView(OwnerPortalRequiredMixin, View):
    template_name = "owner_portal/statement.html"

    def get(self, request, public_id):
        account = self.account(request, public_id)
        month = _month(request)
        st = statements.for_owner(account.owner, month)
        if request.GET.get("format") == "pdf":
            if st is None:
                raise Http404
            response = HttpResponse(render_statement(st), content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{owners.statement_filename(st)}"'
            return _no_store(response)
        last = statements.default_month()
        months = [add_months(last, -i) for i in range(PICKER_MONTHS)]
        if month not in months:
            months.append(month)
        return _no_store(render(request, self.template_name, {
            "account": account, "st": st, "month": month, "months": months}))


class SentPdfView(OwnerPortalRequiredMixin, View):
    """A statement PDF exactly as the office sent it."""

    def get(self, request, public_id):
        send = selectors.own_send(request.user, public_id)
        if send is None:
            raise Http404
        try:
            handle = send.pdf.open("rb")
        except FileNotFoundError:
            raise Http404 from None
        response = FileResponse(handle, content_type="application/pdf", filename=f"{send.number}.pdf")
        response["X-Robots-Tag"] = "noindex, nofollow"
        return _no_store(response)


class AcceptView(View):
    """Open to anyone with the link; claiming it needs a login with the invited, verified phone."""

    template_name = "owner_portal/accept.html"

    def get(self, request, token):
        invitation = services.get_invitation(token)
        if invitation is None or not invitation.is_pending:
            return _no_store(render(request, self.template_name, {"invalid": True}, status=404))
        request.session[OWNER_INVITE_KEY] = token
        return _no_store(render(request, self.template_name, {"invitation": invitation}))

    def post(self, request, token):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('accounts:login')}?next={request.path}")
        if not request.user.phone_verified:
            return redirect("accounts:verify_phone")
        try:
            account = services.accept_invitation(token, request.user, request=request)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _error_text(exc))
            return redirect("owner_portal:accept", token=token)
        request.session.pop(OWNER_INVITE_KEY, None)
        messages.success(request, _("Welcome. Here are your properties with %(org)s.")
                         % {"org": account.organization.name})
        return redirect("owner_portal:account", public_id=account.public_id)


# ---------------------------------------------------------------------------
# Staff: on the owner page
# ---------------------------------------------------------------------------


def _staff_owner(request, public_id) -> PropertyOwner:
    return get_object_or_404(PropertyOwner, organization=request.organization, public_id=public_id)


def _back(owner):
    return redirect("properties:owner_edit", public_id=owner.public_id)


class InviteView(CapabilityRequiredMixin, View):
    required_capability = "owners.invite_portal"

    def post(self, request, public_id):
        owner = _staff_owner(request, public_id)
        try:
            invitation, token = services.invite_owner(request.membership, owner, request=request)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _error_text(exc))
            return _back(owner)
        url = request.build_absolute_uri(reverse("owner_portal:accept", args=[token]))
        if not services.send_invitation(invitation, url):
            messages.warning(request, _("The invitation was made but could not be sent. Try again."))
        else:
            sent = [_("SMS to %(phone)s") % {"phone": invitation.phone}] if invitation.sent_by_sms else []
            if invitation.sent_by_email:
                sent.append(_("email to %(email)s") % {"email": invitation.email})
            messages.success(request, _("Invitation sent by %(how)s. It works for 7 days.")
                             % {"how": _(" and ").join(sent)})
        return _back(owner)


class RevokeInvitationView(CapabilityRequiredMixin, View):
    required_capability = "owners.invite_portal"

    def post(self, request, public_id):
        owner = _staff_owner(request, public_id)
        invitation = services.pending_invitation(owner)
        if invitation is not None:
            try:
                services.revoke_invitation(request.membership, invitation, request=request)
            except PermissionDenied as exc:
                messages.error(request, _error_text(exc))
                return _back(owner)
            messages.success(request, _("Invitation cancelled."))
        return _back(owner)


class RevokeAccountView(CapabilityRequiredMixin, View):
    required_capability = "owners.invite_portal"

    def post(self, request, public_id):
        owner = _staff_owner(request, public_id)
        account = services.live_account(owner)
        if account is not None:
            try:
                services.revoke_account(request.membership, account, request=request)
            except PermissionDenied as exc:
                messages.error(request, _error_text(exc))
                return _back(owner)
            messages.success(request, _("Owner portal access removed."))
        return _back(owner)


def owner_card(owner: PropertyOwner) -> dict:
    """What the owner page shows about the owner portal."""
    return {"account": services.live_account(owner), "invitation": services.pending_invitation(owner)}
