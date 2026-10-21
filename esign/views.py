"""E-signature pages (D-118): the request page for staff, the tenant's signing page and the public check page."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from accounts.permissions import can
from core import ratelimit
from core.ids import uuid_or_none
from core.net import client_ip
from core.phone import mask_phone
from leases.models import Lease
from leases.services import visible_leases

from . import services
from .models import Signer, SigningRequest

PAGE_RATE_LIMIT = 30  # per IP per minute, on the public pages
CODE_CHECKS_PER_IP = 20  # per IP per 10 minutes


def _error_text(exc) -> str:
    if isinstance(exc, ValidationError):
        return " ".join(exc.messages)
    return str(exc)


def _pdf(field, filename: str, *, download: bool = False):
    try:
        handle = field.open("rb")
    except FileNotFoundError:
        raise Http404 from None
    response = FileResponse(handle, content_type="application/pdf", as_attachment=download, filename=filename)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _public(response):
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Referrer-Policy"] = "no-referrer"
    return response


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------


def _get_request(request, public_id) -> SigningRequest:
    qs = services.visible_requests(request.membership).select_related(
        "organization", "lease__unit__property", "created_by", "closed_by", "document")
    req = get_object_or_404(qs, public_id=public_id)
    if req.kind == SigningRequest.Kind.MOVE_OUT and not can(request.membership, "invoices.view",
                                                            req.lease.unit.property):
        raise Http404
    return req


class SendView(CapabilityRequiredMixin, View):
    """Makes a request for the lease's agreement or move-out statement and sends the tenants their links."""

    required_capability = services.CAPABILITY

    def post(self, request, public_id):
        lease = get_object_or_404(visible_leases(request.membership, Lease.all_objects.all()), public_id=public_id)
        kind = request.POST.get("kind", "")
        try:
            req = services.create(request.membership, lease, kind, share=request.POST.get("share") == "on",
                                  no_renewal_option=request.POST.get("no_renewal_option") == "on", request=request)
        except PermissionDenied:
            raise Http404 from None
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
            return redirect("leases:detail", public_id=lease.public_id)
        _report_links(request, services.send_links(request.membership, req, request=request), req)
        return redirect("esign:detail", public_id=req.public_id)


def _report_links(request, unsent, req):
    if not unsent:
        messages.success(request, _("Sent for signing. Each tenant has an SMS with their link."))
        return
    for signer, url in unsent:
        messages.warning(request, _("No SMS went to %(name)s (SMS stopped or not delivered). Share this link with "
                                    "them yourself; it is shown only now: %(url)s") % {"name": signer.name,
                                                                                         "url": url})


class DetailView(CapabilityRequiredMixin, View):
    template_name = "esign/detail.html"
    required_capability = "leases.view"

    def get(self, request, public_id):
        req = _get_request(request, public_id)
        can_act = req.is_open and can(request.membership, services.CAPABILITY, req.lease.unit.property)
        signers = list(req.signers.select_related("signed_by"))
        return render(request, self.template_name, {
            "req": req, "lease": req.lease, "signers": signers, "can_act": can_act,
            "landlord": next((s for s in signers if s.role == Signer.Role.LANDLORD), None),
            "check_url": services.check_url(req), "keep_until": services.keep_until(req),
            "can_evidence": can(request.membership, services.CAPABILITY, req.lease.unit.property),
        })

    def post(self, request, public_id):
        req = _get_request(request, public_id)
        action = request.POST.get("action", "")
        try:
            if action == "sign":
                req = services.sign_as_landlord(request.membership, req, typed_name=request.POST.get("typed_name", ""),
                                                agreed=request.POST.get("agree") == "on", request=request)
                messages.success(request, _("Signed for the landlord.") if req.status != req.Status.COMPLETED
                                 else _("Signed by everyone. The signed copy is in the lease's documents."))
            elif action == "link":
                signer_id = uuid_or_none(request.POST.get("signer"))
                signer = get_object_or_404(req.signers.all(), public_id=signer_id) if signer_id else None
                if signer is None:
                    raise Http404
                url, sent = services.new_link(request.membership, signer, request=request)
                _report_links(request, [] if sent else [(signer, url)], req)
            elif action == "cancel":
                services.cancel(request.membership, req, request.POST.get("reason", ""), request=request)
                messages.success(request, _("Request cancelled. The links no longer work."))
            else:
                raise Http404
        except PermissionDenied:
            raise Http404 from None
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
        return redirect("esign:detail", public_id=req.public_id)


class PdfView(CapabilityRequiredMixin, View):
    """The document as sent, or the signed copy (?signed=1)."""

    required_capability = "leases.view"

    def get(self, request, public_id):
        req = _get_request(request, public_id)
        if request.GET.get("signed") == "1":
            if not req.signed_pdf:
                raise Http404
            return _pdf(req.signed_pdf, f"{req.number}-signed.pdf", download=request.GET.get("download") == "1")
        return _pdf(req.original_pdf, f"{req.number}.pdf", download=request.GET.get("download") == "1")


class EvidenceView(CapabilityRequiredMixin, View):
    """A ZIP of the files, the signers, the audit trail and an Evidence Act s.106B certificate to fill in."""

    required_capability = services.CAPABILITY

    def get(self, request, public_id):
        req = _get_request(request, public_id)
        try:
            content = services.evidence_pack(request.membership, req, request=request)
        except PermissionDenied:
            raise Http404 from None
        response = HttpResponse(content, content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{req.number}-evidence.zip"'
        response["Cache-Control"] = "private, no-store"
        return response


# ---------------------------------------------------------------------------
# The tenant's link
# ---------------------------------------------------------------------------


def _can_read(req: SigningRequest) -> bool:
    """The signer may open the document while it waits, after they declined it, and once it is signed."""
    return bool(req.signed_pdf) or req.is_open or req.status == SigningRequest.Status.DECLINED


class SignView(View):
    """Open to anyone with the link. A code sent to the tenant's phone must be entered before anything is shown."""

    template_name = "esign/sign.html"

    def _signer(self, request, token) -> Signer | None:
        signer = services.signer_for_token(token)
        if signer is not None and signer.request.status == SigningRequest.Status.PENDING:
            services.open_requests(signer.request.lease)  # marks the request expired once its day has passed
            signer.request.refresh_from_db()
        return signer

    def _render(self, request, signer, status=200, **extra):
        req = signer.request
        verified = services.is_verified(request.session, signer)
        others = [s for s in req.signers.all() if s.pk != signer.pk] if verified else []
        completed = req.status == SigningRequest.Status.COMPLETED
        can_sign = req.is_open and not signer.has_signed and not signer.declined_at
        return _public(render(request, self.template_name, {
            "signer": signer, "req": req, "verified": verified, "others": others,
            "can_sign": can_sign, "can_verify": can_sign or completed, "can_read": _can_read(req),
            "phone": mask_phone(signer.phone), "token": self.kwargs["token"], **extra}, status=status))

    def get(self, request, token):
        if not ratelimit.hit(f"esign:page:ip:{client_ip(request)}", PAGE_RATE_LIMIT, 60):
            return HttpResponse(_("Too many requests. Try again in a minute."), status=429)
        signer = self._signer(request, token)
        if signer is None:
            return _public(render(request, "esign/sign_unknown.html", status=404))
        return self._render(request, signer)

    def post(self, request, token):
        if not ratelimit.hit(f"esign:page:ip:{client_ip(request)}", PAGE_RATE_LIMIT, 60):
            return HttpResponse(_("Too many requests. Try again in a minute."), status=429)
        signer = self._signer(request, token)
        if signer is None:
            return _public(render(request, "esign/sign_unknown.html", status=404))
        action = request.POST.get("action", "")
        can_sign = signer.request.is_open and not signer.has_signed and not signer.declined_at
        try:
            if action == "send_code":
                if not can_sign and signer.request.status != SigningRequest.Status.COMPLETED:
                    raise ValidationError(_("This request is closed."))
                services.send_code(signer)
                messages.success(request, _("A code was sent by SMS to %(phone)s.")
                                 % {"phone": mask_phone(signer.phone)})
            elif action == "check_code":
                if not ratelimit.hit(f"esign:check:ip:{client_ip(request)}", CODE_CHECKS_PER_IP, 600):
                    raise ValidationError(_("Too many tries. Wait 10 minutes."))
                if not services.check_code(signer, request.POST.get("code", "")):
                    raise ValidationError(_("That code is wrong or has expired. Ask for a new one."))
                services.mark_verified(request.session, signer)
            elif action in ("sign", "decline"):
                if not services.is_verified(request.session, signer):
                    raise ValidationError(_("Enter the code sent to your phone first."))
                if action == "sign":
                    req = services.sign_as_tenant(signer, typed_name=request.POST.get("typed_name", ""),
                                                  agreed=request.POST.get("agree") == "on", request=request)
                    messages.success(request, _("Thank you. You have signed.") if req.status != req.Status.COMPLETED
                                     else _("Thank you. Everyone has signed; you can download the signed copy."))
                else:
                    services.decline(signer, request.POST.get("reason", ""), request=request)
                    messages.success(request, _("You have declined to sign. The office has been told."))
            else:
                raise ValidationError(_("Something went wrong. Try again."))
        except services.SigningError as exc:
            messages.error(request, str(exc))
        except ValidationError as exc:
            messages.error(request, _error_text(exc))
        except PermissionDenied:
            messages.error(request, _("Enter the code sent to your phone first."))
        return redirect("esign_sign", token=token)


class SignPdfView(View):
    """The document for a signer who entered their code: as sent, or the signed copy once everyone signed."""

    def get(self, request, token):
        if not ratelimit.hit(f"esign:page:ip:{client_ip(request)}", PAGE_RATE_LIMIT, 60):
            return HttpResponse(_("Too many requests. Try again in a minute."), status=429)
        signer = services.signer_for_token(token)
        if signer is None or not services.is_verified(request.session, signer):
            raise Http404
        req = signer.request
        if req.signed_pdf:
            return _pdf(req.signed_pdf, f"{req.number}-signed.pdf", download=request.GET.get("download") == "1")
        if not _can_read(req):
            raise Http404
        return _pdf(req.original_pdf, f"{req.number}.pdf", download=request.GET.get("download") == "1")


# ---------------------------------------------------------------------------
# Checking a copy
# ---------------------------------------------------------------------------


class CheckView(View):
    """The public page behind the code in the signing record. No login: the code is the key."""

    template_name = "esign/check.html"

    def get(self, request, code):
        if not ratelimit.hit(f"esign:check_page:ip:{client_ip(request)}", PAGE_RATE_LIMIT, 60):
            return HttpResponse(_("Too many requests. Try again in a minute."), status=429)
        req = SigningRequest.objects.select_related("organization").filter(verify_code=code).first()
        if req is None:
            return _public(render(request, "esign/sign_unknown.html", status=404))
        signers = list(req.signers.all())
        for s in signers:
            s.masked_phone = mask_phone(s.phone)
        return _public(render(request, self.template_name, {"req": req, "signers": signers}))
