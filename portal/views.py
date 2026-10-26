"""The tenant portal (D-055) and the staff actions that invite tenants to it.

Portal pages see only `selectors.own_*`: anything outside the user's own leases is a 404.
"""


from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin, VerifiedUserRequiredMixin
from accounts.views import PORTAL_INVITE_KEY
from ai_assistant import tenant as tenant_assistant
from billing import deposits
from billing.invoicing import lease_balance
from core import charts, exports, ratelimit
from documents.views import file_response
from esign import services as esign
from esign.models import SigningRequest
from esign.views import _pdf as pdf_response
from leases import forms as lease_forms
from leases import services as lease_services
from leases.models import Lease
from maintenance import forms as maintenance_forms
from maintenance import services as maintenance
from maintenance.models import MaintenanceRequest
from maintenance.views import photo_response
from payments.forms import accounts_for
from payments.models import PaymentAccount
from reports import patterns
from tenants.models import Tenant
from tenants.services import visible_tenants

from . import conversation, offers, own_use, selectors, services
from . import forms as portal_forms
from .models import OwnUse


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
        unread = conversation.unread_for_tenant(request.user)
        cards = []
        for lease in leases:
            active = lease.status == Lease.Status.ACTIVE
            repairs = list(selectors.own_requests(request.user).filter(
                lease=lease, status__in=MaintenanceRequest.UNFINISHED)[:3])
            cards.append({
                "summary": selectors.summary(lease),
                "lease": lease,
                "months": _rent_months(lease),
                "payments": selectors.payments(lease)[:3],
                "pending": selectors.pending_payments(lease),
                "repairs": repairs,
                "unread": unread.get(lease.pk, 0),
                "offer": offers.open_offer(lease) if active else None,
                "notice": lease_services.open_notice(lease) if active else None,
                "signings": [(r, s) for r, s in esign.portal_requests(request.user, lease)
                             if r.is_open and not s.has_signed and not s.declined_at],
                "can_report": active,
                "ways": _pay_ways(lease) if active or lease_balance(lease) > 0 else [],
                "news": conversation.received(lease)[:2],
                "rent": lease.rent_on(timezone.localdate()),
                "days_left": (lease.end_date - timezone.localdate()).days if active and lease.end_date else None,
                "deposit": _deposit_statement(lease),
                "documents": selectors.own_documents(lease),
            })
        return _no_store(render(request, self.template_name, {
            "cards": cards,
            "summaries": cards,
            "ussd_code": getattr(settings, "USSD_CODE", ""),
            "can_ask": tenant_assistant.portal_caller(request.user) is not None,
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
            "documents": selectors.own_documents(lease),
            "notice": lease_services.open_notice(lease) if lease.status == Lease.Status.ACTIVE else None,
            "months": _rent_months(lease),
            "rent": lease.rent_on(timezone.localdate()),
            "offer": offers.open_offer(lease) if lease.status == Lease.Status.ACTIVE else None,
            "unread": conversation.unread_for_tenant(request.user).get(lease.pk, 0),
            "deposit": _deposit_statement(lease),
            "signings": [(r, s) for r, s in esign.portal_requests(request.user, lease)
                         if r.is_open and not s.has_signed and not s.declined_at],
        }))


class SigningView(PortalRequiredMixin, View):
    """Read and sign an agreement or move-out statement the office sent, without the link and SMS code (D-171).

    The tenant is already signed in with their verified phone; they type their password again to sign."""

    template_name = "portal/sign.html"

    def load(self, request, public_id, request_id):
        lease = selectors.own_lease(request.user, public_id)
        if lease is None:
            raise Http404
        req = SigningRequest.objects.select_related("organization").filter(lease=lease, public_id=request_id).first()
        signer = esign.portal_signer(request.user, req) if req is not None else None
        if signer is None:
            raise Http404
        esign.open_requests(lease)  # marks a request past its day as expired
        req.refresh_from_db()
        return lease, req, signer

    def get(self, request, public_id, request_id):
        lease, req, signer = self.load(request, public_id, request_id)
        can_sign = req.is_open and not signer.has_signed and not signer.declined_at
        return _no_store(render(request, self.template_name, {
            "lease": lease, "req": req, "signer": signer, "can_sign": can_sign,
            "others": [s for s in req.signers.all() if s.pk != signer.pk]}))

    def post(self, request, public_id, request_id):
        lease, req, signer = self.load(request, public_id, request_id)
        action = request.POST.get("action", "")
        try:
            if action == "sign":
                if not ratelimit.hit(f"esign:portal:{request.user.pk}", 10, 600):
                    raise ValidationError(_("Too many tries. Wait 10 minutes."))
                if not request.user.check_password(request.POST.get("password", "")):
                    raise ValidationError(_("That password is not right."))
                done = esign.sign_in_portal(request.user, req, typed_name=request.POST.get("typed_name", ""),
                                            agreed=request.POST.get("agree") == "on", request=request)
                messages.success(request, _("Thank you. You have signed.") if done.status != done.Status.COMPLETED
                                 else _("Thank you. Everyone has signed; the signed copy is below."))
            elif action == "decline":
                esign.decline_in_portal(request.user, req, request.POST.get("reason", ""), request=request)
                messages.success(request, _("You have declined to sign. The office has been told."))
            else:
                raise ValidationError(_("Something went wrong. Try again."))
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
        except PermissionDenied:
            raise Http404 from None
        return redirect("portal:signing", public_id=public_id, request_id=request_id)


class SigningPdfView(SigningView):
    def get(self, request, public_id, request_id):
        lease, req, signer = self.load(request, public_id, request_id)
        download = request.GET.get("download") == "1"
        if req.signed_pdf:
            return pdf_response(req.signed_pdf, f"{req.number}-signed.pdf", download=download)
        if not req.is_open:
            raise Http404
        return pdf_response(req.original_pdf, f"{req.number}.pdf", download=download)


def _pay_ways(lease):
    """Where the tenant can pay by hand: Paybill and Till numbers, bank accounts, with the unit's account reference (D-100)."""
    reference = lease.unit.payment_reference
    ways = []
    for account in accounts_for(lease).exclude(type=PaymentAccount.Type.CASH).exclude(number="")[:3]:
        kind = account.type
        if kind == PaymentAccount.Type.PAYBILL:
            ways.append({"title": _("M-Pesa Paybill"), "number_label": _("Business number"), "number": account.number,
                         "reference": reference})
        elif kind == PaymentAccount.Type.TILL:
            ways.append({"title": _("M-Pesa Buy Goods"), "number_label": _("Till number"), "number": account.number})
        elif kind == PaymentAccount.Type.BANK:
            ways.append({"title": account.display_name, "number_label": _("Account number"), "number": account.number,
                         "reference": reference})
        else:
            ways.append({"title": "M-Pesa", "number_label": _("Send to"), "number": account.number})
    return ways


def _deposit_statement(lease):
    """What the tenant sees of their deposit (D-150): held, each deduction with its reason, and the refund deadline."""
    if not lease.organization.takes_deposits:
        return None
    clearance = deposits.clearance_statement(lease)
    if not clearance["types"]:
        return None
    due = deposits.settle_by(lease) if clearance["held"] > 0 else None
    return {"clearance": clearance, "settle_by": due,
            "overdue": due is not None and due < timezone.localdate()}


class PaymentsView(PortalRequiredMixin, View):
    """Every payment on the lease, by year, with a CSV download."""

    template_name = "portal/payments.html"

    def get(self, request, public_id):
        lease = selectors.own_lease(request.user, public_id)
        if lease is None:
            raise Http404
        years = selectors.payment_years(lease)
        try:
            year = int(request.GET.get("year", ""))
        except ValueError:
            year = None
        if year not in years:
            year = None
        query = request.GET.get("q", "").strip()[:60]
        found = selectors.payment_history(lease, year, query)
        if request.GET.get("download") == "csv":
            return self._csv(lease, found, year)
        total = found.aggregate(total=Sum("amount"))["total"] or 0
        return _no_store(render(request, self.template_name, {
            "lease": lease, "payments": found, "years": years, "year": year, "total": total, "query": query,
            "summary": selectors.summary(lease), "pending": selectors.pending_payments(lease),
            "kinds": selectors.totals_by_kind(found), "can_write": lease.status == Lease.Status.ACTIVE,
        }))

    def _csv(self, lease, found, year):
        suffix = f"-{year}" if year else ""
        response = exports.csv_response(f"payments-{lease.number or 'lease'}{suffix}.csv")
        writer = exports.writer(response)
        writer.writerow([_("Date"), _("Method"), _("Reference"), _("Amount"), _("Receipt")])
        for payment in found:
            receipt = getattr(payment, "receipt", None)
            writer.writerow([payment.paid_at.isoformat(), payment.get_method_display(), payment.reference,
                             payment.amount, receipt.number if receipt and receipt.pdf else ""])
        return _no_store(response)


class UtilitiesView(PortalRequiredMixin, View):
    """Month by month: the water the office bills, and the tenant's own private notes on electricity or water
    they pay for themselves (D-219). The notes never reach staff, invoices or reports."""

    template_name = "portal/utilities.html"

    def _context(self, request, lease, form=None):
        own = [own_use.summarize(lease, u) for u in (OwnUse.Utility.ELECTRICITY, OwnUse.Utility.WATER)]
        specs = {}
        for item in own:
            if not item.has_data:
                continue
            labels = [m.month.strftime("%b %y") for m in item.months]
            specs[f"chart-{item.utility}-units"] = charts.chart(
                "bar", labels, [charts.series(item.unit_label, [m.units for m in item.months], "1")], axis="count")
        billed = own_use.billed_water(lease)
        bill_spec = None
        if billed:
            ordered = list(reversed(billed))
            bill_spec = charts.chart("bar", [b.month.strftime("%b %y") for b in ordered],
                                     [charts.series(_("Billed"), [b.amount for b in ordered], "1")],
                                     currency=lease.currency)
        return {"lease": lease, "summary": selectors.summary(lease), "own": own, "specs": specs, "billed": billed,
                "bill_spec": bill_spec, "can_write": lease.status == Lease.Status.ACTIVE,
                "form": form or portal_forms.OwnUseForm(initial={"on": timezone.localdate(), "kind": "READING",
                                                                  "utility": request.GET.get("utility", "ELECTRICITY")})}

    def get(self, request, public_id):
        lease = selectors.own_lease(request.user, public_id)
        if lease is None:
            raise Http404
        return _no_store(render(request, self.template_name, self._context(request, lease)))

    def post(self, request, public_id):
        lease = selectors.own_lease(request.user, public_id)
        if lease is None or lease.status != Lease.Status.ACTIVE:
            raise Http404
        form = portal_forms.OwnUseForm(request.POST)
        if form.is_valid():
            try:
                own_use.add_entry(request.user, lease, **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, _error_text(exc))
            else:
                messages.success(request, _("Saved. Only you can see this."))
                return redirect("portal:utilities", public_id=lease.public_id)
        return _no_store(render(request, self.template_name, self._context(request, lease, form), status=400))


class OwnUseDeleteView(PortalRequiredMixin, View):
    def post(self, request, public_id):
        entry = get_object_or_404(OwnUse, public_id=public_id,
                                  lease_id__in=selectors.own_lease_ids(request.user))
        lease = entry.lease
        own_use.delete_entry(entry)
        messages.success(request, _("Removed."))
        return redirect("portal:utilities", public_id=lease.public_id)


class PaymentReportView(PortalRequiredMixin, View):
    """"Something is wrong with this payment": a note to the office through the lease's messages."""

    def post(self, request, public_id):
        payment = selectors.own_payment(request.user, public_id)
        if payment is None:
            raise Http404
        lease = payment.lease
        note = request.POST.get("note", "").strip()
        about = _("About my payment of %(amount)s on %(day)s%(ref)s:") % {
            "amount": f"{payment.amount:,.2f}", "day": payment.paid_at.strftime("%d %b %Y"),
            "ref": f" ({payment.reference})" if payment.reference else ""}
        try:
            conversation.tenant_send(request.user, lease, f"{about} {note or _('this does not look right.')}")
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _error_text(exc))
            return redirect("portal:payments", public_id=lease.public_id)
        messages.success(request, _("Sent to %(org)s. They will reply in your messages.")
                         % {"org": lease.organization.name})
        return redirect("portal:messages", public_id=lease.public_id)


def _rent_months(lease):
    """The tenant's last 12 months of rent, paid in time or not (D-114); None when no rent was billed."""
    months = patterns.lease_months(lease, timezone.localdate())
    return months if months.billed else None


class LeaseTermsView(PortalRequiredMixin, View):
    """The tenancy agreement as it stands, as a PDF: the terms the tenant signed, without ID numbers (D-131)."""

    def get(self, request, public_id):
        from leases import paperwork, pdf

        lease = selectors.own_lease(request.user, public_id)
        if lease is None:
            raise Http404
        content = pdf.render_agreement(paperwork.agreement(lease, None))
        response = HttpResponse(content, content_type="application/pdf")
        disposition = "attachment" if request.GET.get("download") == "1" else "inline"
        response["Content-Disposition"] = f'{disposition}; filename="{lease.number or "lease"}-agreement.pdf"'
        return _no_store(response)


class MessagesView(PortalRequiredMixin, View):
    """The thread with the office, and the announcements the office sent (D-131)."""

    template_name = "portal/messages.html"

    def _render(self, request, lease, form, status=200):
        response = render(request, self.template_name, {
            "lease": lease, "form": form, "thread": conversation.thread(lease),
            "received": conversation.received(lease), "can_write": lease.status == Lease.Status.ACTIVE,
        }, status=status)
        return _no_store(response)

    def get(self, request, public_id):
        lease = selectors.own_lease(request.user, public_id)
        if lease is None:
            raise Http404
        response = self._render(request, lease, portal_forms.MessageForm())
        if not getattr(request, "viewing_tenant", None):  # the owner looking in must not mark messages read
            conversation.mark_read_by_tenant(lease)
        return response

    def post(self, request, public_id):
        lease = selectors.own_lease(request.user, public_id)
        if lease is None:
            raise Http404
        form = portal_forms.MessageForm(request.POST)
        if form.is_valid():
            try:
                conversation.tenant_send(request.user, lease, form.cleaned_data["body"])
            except ValidationError as exc:
                form.add_error("body" if hasattr(exc, "error_dict") and "body" in exc.error_dict else None, exc)
            except PermissionDenied:
                raise Http404 from None
            else:
                messages.success(request, _("Sent. The office will reply here."))
                return redirect("portal:messages", public_id=lease.public_id)
        return self._render(request, lease, form, status=400)


class OfferAnswerView(PortalRequiredMixin, View):
    """Accept or decline the office's renewal offer (D-131). It changes nothing on the lease until the office act."""

    def post(self, request, public_id):
        from .models import RenewalOffer

        offer = RenewalOffer.objects.filter(public_id=public_id, lease_id__in=selectors.own_lease_ids(request.user)
                                            ).select_related("lease").first()
        if offer is None:
            raise Http404
        choice = request.POST.get("answer")
        if choice not in ("accept", "decline"):
            raise Http404
        try:
            offers.answer(request.user, offer, accept=choice == "accept", request=request)
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
        except PermissionDenied:
            raise Http404 from None
        else:
            messages.success(request, _("Thank you. The office has been told you accepted.") if choice == "accept"
                             else _("Thank you. The office has been told you declined."))
        return redirect("portal:lease", public_id=offer.lease.public_id)


class NoticeCreateView(PortalRequiredMixin, View):
    """The tenant gives notice to vacate (D-106). Changing it afterwards is for the office."""

    template_name = "portal/notice_form.html"

    def load(self, request, public_id) -> Lease:
        lease = selectors.own_lease(request.user, public_id)
        # Estates, water vendors and garbage collectors have no notice to vacate (D-130 item 4).
        if lease is None or lease.status != Lease.Status.ACTIVE or not lease.organization.bills_rent:
            raise Http404
        return lease

    def _render(self, request, lease, form):
        earliest = lease.earliest_move_out(timezone.localdate())
        return _no_store(render(request, self.template_name, {
            "lease": lease, "form": form, "earliest": earliest, "notice": lease_services.open_notice(lease)}))

    def get(self, request, public_id):
        lease = self.load(request, public_id)
        initial = {"move_out_on": lease.earliest_move_out(timezone.localdate()).isoformat()}
        return self._render(request, lease, lease_forms.PortalNoticeForm(initial=initial))

    def post(self, request, public_id):
        lease = self.load(request, public_id)
        form = lease_forms.PortalNoticeForm(request.POST)
        if form.is_valid():
            try:
                notice = lease_services.portal_give_notice(
                    request.user, lease, move_out_on=form.cleaned_data["move_out_on"],
                    reason=form.cleaned_data["reason"], request=request)
            except ValidationError as exc:
                if hasattr(exc, "error_dict"):
                    for name, errors in exc.error_dict.items():
                        form.add_error(name if name in form.fields else None, errors)
                else:
                    form.add_error(None, exc)
            except PermissionDenied:
                raise Http404 from None
            else:
                messages.success(request, _("Your notice is recorded. Move-out day: %(day)s.")
                                 % {"day": notice.move_out_on.strftime("%d %b %Y")})
                return redirect("portal:lease", public_id=lease.public_id)
        return self._render(request, lease, form)


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


class DocumentView(PortalRequiredMixin, View):
    """A document staff shared with the tenant (D-071 item 4)."""

    def get(self, request, public_id):
        doc = selectors.own_document(request.user, public_id)
        if doc is None:
            raise Http404
        return _no_store(file_response(doc))


class ReceiptView(PortalRequiredMixin, View):
    def get(self, request, public_id):
        payment = selectors.own_payment(request.user, public_id)
        receipt = getattr(payment, "receipt", None) if payment else None
        if receipt is None or not receipt.pdf:
            raise Http404
        return _no_store(FileResponse(receipt.pdf.open("rb"), content_type="application/pdf",
                                      as_attachment=request.GET.get("download") == "1",
                                      filename=f"{receipt.number}.pdf"))


class InvoicePdfView(PortalRequiredMixin, View):
    def get(self, request, public_id):
        from billing.pdf import render_invoice

        invoice = selectors.own_invoice(request.user, public_id)
        if invoice is None:
            raise Http404
        response = HttpResponse(render_invoice(invoice), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{invoice.number}.pdf"'
        return _no_store(response)


class NoMatchView(VerifiedUserRequiredMixin, View):
    """Where a tenant lands after verifying their phone when no landlord's records carry that number (D-203)."""

    template_name = "portal/no_match.html"

    def get(self, request):
        if selectors.has_access(request.user):
            return redirect("portal:home")
        return render(request, self.template_name, {"phone": request.user.phone})


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


INVITE_LINK_KEY = "portal_invite_link"


def _staff_tenant(request, public_id) -> Tenant:
    return get_object_or_404(visible_tenants(request.membership, Tenant.all_objects.all()), public_id=public_id)


class InviteView(CapabilityRequiredMixin, View):
    required_capability = "tenants.invite_portal"

    def post(self, request, public_id):
        tenant = _staff_tenant(request, public_id)
        via = request.POST.get("via")
        via = via if via in ("whatsapp", "link") else "sms"
        try:
            invitation, token = services.invite_tenant(request.membership, tenant, request=request,
                                                       via_sms=via == "sms")
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _error_text(exc))
            return redirect("tenants:detail", public_id=tenant.public_id)
        url = request.build_absolute_uri(reverse("portal:accept", args=[token]))
        if via != "sms":
            # The raw token is not stored, so the link is shown once on the tenant page, to this staff member only.
            request.session[INVITE_LINK_KEY] = {
                "tenant": str(tenant.public_id), "url": url, "via": via,
                "whatsapp": services.whatsapp_invite_link(invitation, url) if via == "whatsapp" else ""}
            messages.success(request, _("Invitation ready. It works for 7 days."))
            return redirect("tenants:detail", public_id=tenant.public_id)
        if not services.send_invitation(invitation, url):
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


class RemoveUssdPinView(CapabilityRequiredMixin, View):
    required_capability = "tenants.invite_portal"

    def post(self, request, public_id):
        tenant = _staff_tenant(request, public_id)
        if services.remove_ussd_pin(request.membership, tenant, request=request):
            messages.success(request, _("USSD PIN removed. The tenant can set a new one from the menu."))
        return redirect("tenants:detail", public_id=tenant.public_id)


def tenant_card(tenant: Tenant, request=None) -> dict:
    """What the tenant page shows about the portal and the USSD PIN. With the request, a link made a moment ago by
    WhatsApp or copy is shown once."""
    link = request.session.pop(INVITE_LINK_KEY, None) if request is not None else None
    invitation = services.pending_invitation(tenant)
    return {
        "link": link if link and invitation and link.get("tenant") == str(tenant.public_id) else None,
        "ussd_pin": services.ussd_pin(tenant),
        "account": services.live_account(tenant),
        "invitation": invitation,
        "has_leases": services.has_leases(tenant),
    }
