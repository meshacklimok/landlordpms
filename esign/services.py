"""E-signatures (D-118). Views stay thin; the rules live here.

- A request freezes the PDF and its SHA-256 when it is made. Nothing signs a document that can change.
- Tenants sign by a private link (only its SHA-256 is kept) after a code sent to the phone on file. The code
  is an HMAC, lasts 10 minutes and 5 tries; an attempt is spent before comparing, as in accounts.otp.
- The landlord signs in the app, by a member with `leases.esign`.
- When everyone has signed, the record page is added after the original and the copy is filed on the lease.
"""

import datetime
import hashlib
import hmac
import io
import json
import secrets
import uuid
import zipfile

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import can, require, visible_properties
from audit import services as audit
from audit.models import AuditEvent
from core import ratelimit
from core.numbering import next_number
from core.sms import send_sms
from documents.models import Document
from leases import paperwork
from leases import pdf as lease_pdf
from leases.models import Lease
from notifications import catalog
from notifications.services import channel_allowed
from properties.models import Property

from . import pdf
from .models import Signer, SigningRequest

CAPABILITY = "leases.esign"
Kind = SigningRequest.Kind
Status = SigningRequest.Status

LINK_DAYS = 14
CODE_TTL = datetime.timedelta(minutes=10)
CODE_LENGTH = 6
MAX_ATTEMPTS = 5
RESEND_COOLDOWN = datetime.timedelta(seconds=60)
MAX_CODES_PER_HOUR = 5
VERIFIED_FOR = datetime.timedelta(minutes=30)
SESSION_KEY = "esign_verified"
SHORT_LEASE_YEARS = 2  # Land Act 2012 s.58: a short-term lease may be oral or written and is not registered
KEEP_YEARS = 6  # Limitation of Actions Act s.4 (contract claims); covers the 5 years of Tax Procedures Act s.23


class SigningError(Exception):
    """Something the signer can fix or wait out; the message is shown to them."""


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def visible_requests(membership: Membership, queryset=None):
    qs = (queryset if queryset is not None else SigningRequest.objects.all()).filter(
        organization=membership.organization)
    return qs.filter(lease__unit__property__in=visible_properties(membership, Property.all_objects.all()))


def _check(actor: Membership, lease: Lease) -> None:
    if lease.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, CAPABILITY, lease.unit.property)


def _audit(action, req: SigningRequest, request, *, actor=None, changes=None):
    audit.record(action, actor=actor, organization=req.organization, obj=req, request=request,
                 changes=changes or {})


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sign_url(token: str) -> str:
    return settings.SITE_URL.rstrip("/") + reverse("esign_sign", args=[token])


def check_url(req: SigningRequest) -> str:
    return settings.SITE_URL.rstrip("/") + reverse("esign_check", args=[req.verify_code])


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def _add_years(day: datetime.date, years: int) -> datetime.date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def is_long_lease(lease: Lease) -> bool:
    """A fixed term of more than two years. Such a lease is registered, and the Law of Contract Act s.3(3) wants
    it signed before a witness, by hand or by a certified (advanced) electronic signature, which this is not."""
    return lease.end_date is not None and lease.end_date > _add_years(lease.start_date, SHORT_LEASE_YEARS)


def keep_until(req: SigningRequest) -> datetime.date | None:
    """Six years after the lease ends, or after signing if that is later. None while the lease has no end."""
    lease_end = req.lease.ended_on or req.lease.end_date
    if lease_end is None:
        return None
    signed = timezone.localdate(req.completed_at) if req.completed_at else lease_end
    return _add_years(max(lease_end, signed), KEEP_YEARS)


def send_problem(lease: Lease, kind: str) -> str | None:
    """Why this document cannot be sent for signing now, or None."""
    if lease.archived_at is not None:
        return _("The lease is archived.")
    if kind == Kind.AGREEMENT and lease.status not in (Lease.Status.DRAFT, Lease.Status.ACTIVE):
        return _("Only a draft or active lease's agreement can be signed.")
    if kind == Kind.AGREEMENT and is_long_lease(lease):
        return _("This lease runs for more than two years, so the law wants it signed before a witness and "
                 "registered. Print the agreement and sign it on paper instead.")
    if kind == Kind.MOVE_OUT and not paperwork.move_out_applies(lease):
        return _("The move-out statement applies once notice is given or the lease has ended.")
    tenants = paperwork._tenants(lease)
    if not tenants:
        return _("The lease has no tenants.")
    missing = [t.name for t in tenants if not t.phone]
    if missing:
        return _("Add a phone number for %(names)s first: the signing code goes to it.") % {
            "names": ", ".join(missing)}
    return None


def can_send(membership: Membership, lease: Lease, kind: str) -> bool:
    prop = lease.unit.property
    if not can(membership, CAPABILITY, prop):
        return False
    return kind != Kind.MOVE_OUT or can(membership, "invoices.view", prop)


def _expire_stale(lease: Lease, kind: str | None = None) -> None:
    qs = SigningRequest.objects.filter(lease=lease, status=Status.PENDING, expires_at__lte=timezone.now())
    if kind:
        qs = qs.filter(kind=kind)
    qs.update(status=Status.EXPIRED, closed_at=timezone.now())


def open_requests(lease: Lease) -> dict:
    """The waiting request of each kind, by kind."""
    _expire_stale(lease)
    return {r.kind: r for r in SigningRequest.objects.filter(lease=lease, status=Status.PENDING)}


def lease_panel(membership: Membership, lease: Lease) -> dict | None:
    """What the lease page shows: what may be sent for signing now, and the latest requests."""
    prop = lease.unit.property
    waiting = open_requests(lease)
    requests = list(SigningRequest.objects.filter(lease=lease)[:5])
    if not can(membership, "invoices.view", prop):
        requests = [r for r in requests if r.kind != Kind.MOVE_OUT]
    sendable = []
    if lease.archived_at is None:
        for kind in Kind:
            if kind == Kind.AGREEMENT and lease.status not in (Lease.Status.DRAFT, Lease.Status.ACTIVE):
                continue
            if kind == Kind.MOVE_OUT and not paperwork.move_out_applies(lease):
                continue
            if can_send(membership, lease, kind):
                sendable.append({"kind": kind.value, "label": kind.label, "waiting": waiting.get(kind.value),
                                 "problem": send_problem(lease, kind)})
    if not requests and not sendable:
        return None
    return {"requests": requests, "sendable": sendable,
            "can_share": can(membership, "documents.share_with_tenant", prop)}


def _document(actor: Membership, lease: Lease, kind: str) -> tuple[bytes, str]:
    if kind == Kind.AGREEMENT:
        data = {**paperwork.agreement(lease, actor), "for_signing": True}
        return lease_pdf.render_agreement(data), _("Tenancy agreement %(lease)s") % {"lease": lease}
    data = {**paperwork.move_out(lease), "for_signing": True}
    title = _("Move-out statement %(lease)s, %(day)s") % {"lease": lease, "day": data["today"].strftime("%d %b %Y")}
    return lease_pdf.render_move_out(data), title


@transaction.atomic
def create(actor: Membership, lease: Lease, kind: str, *, share: bool = False, no_renewal_option: bool = False,
           request=None) -> SigningRequest:
    """Freezes the document and makes a signer for the landlord and each tenant. Links are sent separately.

    For the agreement the sender confirms the lease gives no option to renew: the app cannot see one in the
    free-text terms, and with it the lease is no longer short-term (Land Act 2012 s.58)."""
    _check(actor, lease)
    if kind not in Kind.values or not can_send(actor, lease, kind):
        raise PermissionDenied(CAPABILITY)
    lease = (Lease.all_objects.select_for_update(of=("self",))
             .select_related("unit__property", "organization").get(pk=lease.pk))
    problem = send_problem(lease, kind)
    if problem:
        raise ValidationError(problem)
    if kind == Kind.AGREEMENT and not no_renewal_option:
        raise ValidationError(_("Confirm the lease gives no option to renew. A lease with one must be signed on "
                                "paper before a witness."))
    no_renewal_option = kind == Kind.AGREEMENT
    if share and not can(actor, "documents.share_with_tenant", lease.unit.property):
        raise PermissionDenied("documents.share_with_tenant")
    _expire_stale(lease, kind)
    if SigningRequest.objects.filter(lease=lease, kind=kind, status=Status.PENDING).exists():
        raise ValidationError(_("This document is already waiting for signatures. Cancel that request first."))

    content, title = _document(actor, lease, kind)
    now = timezone.now()
    req = SigningRequest(
        organization=lease.organization, lease=lease, kind=kind, title=title[:150],
        number=next_number(lease.organization, "esign", prefix="SIG", period=str(timezone.localdate().year)),
        original_sha256=_sha256(content), expires_at=now + datetime.timedelta(days=LINK_DAYS),
        share_with_tenant=share, no_renewal_option=no_renewal_option, created_by=actor.user,
        verify_code=secrets.token_urlsafe(16))
    req.original_pdf.save(f"{uuid.uuid4().hex}.pdf", ContentFile(content), save=False)
    req.save()
    Signer.objects.create(organization=req.organization, request=req, role=Signer.Role.LANDLORD, position=0,
                          name=lease.organization.name)
    for position, tenant in enumerate(paperwork._tenants(lease), start=1):
        Signer.objects.create(organization=req.organization, request=req, role=Signer.Role.TENANT,
                              position=position, tenant=tenant, name=tenant.name, phone=tenant.phone)
    _audit("esign.create", req, request, actor=actor.user, changes={
        "kind": [None, kind], "number": [None, req.number], "sha256": [None, req.original_sha256],
        "share_with_tenant": [None, share], "no_renewal_option": [None, no_renewal_option]})
    return req


def _staff_request(actor: Membership, req: SigningRequest) -> None:
    if req.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, CAPABILITY, req.lease.unit.property)


def new_link(actor: Membership, signer: Signer, *, request=None) -> tuple[str, bool]:
    """A fresh link for a tenant (the old one stops working). Returns the link and whether the SMS went."""
    req = signer.request
    _staff_request(actor, req)
    if not req.is_open or signer.role != Signer.Role.TENANT or signer.has_signed or signer.declined_at:
        raise ValidationError(_("This signer has nothing left to sign."))
    token = secrets.token_urlsafe(24)
    url = sign_url(token)
    sent = False
    if channel_allowed(signer.tenant, catalog.SMS):
        sent = send_sms(signer.phone, _(
            "%(org)s asks you to sign %(title)s electronically. Open %(url)s . The link works until %(day)s.")
            % {"org": req.organization.name, "title": req.title, "url": url,
               "day": timezone.localtime(req.expires_at).strftime("%d %b %Y")}, org=req.organization).ok
    Signer.objects.filter(pk=signer.pk).update(token_hash=_hash_token(token), link_sent_at=timezone.now(),
                                               code_hash="", code_expires_at=None)
    _audit("esign.send_link", req, request, actor=actor.user, changes={"signer": [None, signer.name],
                                                                        "sms_sent": [None, sent]})
    return url, sent


def send_links(actor: Membership, req: SigningRequest, *, request=None) -> list[tuple[Signer, str]]:
    """Links for every tenant who has not signed; returns those whose SMS did not go, with their link."""
    unsent = []
    for signer in req.signers.filter(role=Signer.Role.TENANT, signed_at__isnull=True, declined_at__isnull=True):
        url, sent = new_link(actor, signer, request=request)
        if not sent:
            unsent.append((signer, url))
    return unsent


@transaction.atomic
def cancel(actor: Membership, req: SigningRequest, reason: str, *, request=None) -> SigningRequest:
    _staff_request(actor, req)
    reason = (reason or "").strip()[:300]
    if not reason:
        raise ValidationError(_("Say why the request is cancelled."))
    req = SigningRequest.objects.select_for_update().get(pk=req.pk)
    if req.status != Status.PENDING:
        raise ValidationError(_("Only a request still waiting for signatures can be cancelled."))
    req.status, req.closed_at, req.closed_by, req.close_reason = Status.CANCELLED, timezone.now(), actor.user, reason
    req.save(update_fields=["status", "closed_at", "closed_by", "close_reason", "updated_at"])
    req.signers.update(token_hash="", code_hash="")
    _audit("esign.cancel", req, request, actor=actor.user, changes={"reason": [None, reason]})
    return req


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


def signer_for_token(token: str) -> Signer | None:
    if not token:
        return None
    return (Signer.objects.select_related("request__organization", "request__lease__unit__property", "tenant")
            .filter(token_hash=_hash_token(token), role=Signer.Role.TENANT).first())


def _code_hash(signer: Signer, code: str) -> str:
    msg = f"esign:{signer.pk}:{signer.phone}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def send_code(signer: Signer) -> None:
    """Sends a signing code to the phone on file. A security message: it goes even if the tenant stopped SMS."""
    now = timezone.now()
    if signer.code_sent_at and now - signer.code_sent_at < RESEND_COOLDOWN:
        raise SigningError(_("Please wait a minute before asking for another code."))
    if not ratelimit.hit(f"esign:code:{signer.pk}", MAX_CODES_PER_HOUR, 3600):
        raise SigningError(_("Too many codes asked for. Try again in an hour."))
    code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
    Signer.objects.filter(pk=signer.pk).update(code_hash=_code_hash(signer, code), code_sent_at=now,
                                               code_expires_at=now + CODE_TTL, code_attempts=0)
    signer.code_sent_at = now
    result = send_sms(signer.phone, _("Your code to sign %(title)s is %(code)s. It expires in 10 minutes. "
                                      "Never share it.") % {"title": signer.request.title[:60], "code": code},
                      org=signer.request.organization)
    if not result.ok:
        raise SigningError(_("The code could not be sent. Try again in a minute."))


def check_code(signer: Signer, code: str) -> bool:
    now = timezone.now()
    current = Signer.objects.filter(pk=signer.pk, code_expires_at__gt=now).exclude(code_hash="").first()
    if current is None:
        return False
    # Spend an attempt before comparing, in one statement, so parallel guesses can't share one.
    if not Signer.objects.filter(pk=signer.pk, code_attempts__lt=MAX_ATTEMPTS).update(
            code_attempts=F("code_attempts") + 1):
        return False
    if not hmac.compare_digest(current.code_hash, _code_hash(signer, (code or "").strip())):
        return False
    Signer.objects.filter(pk=signer.pk).update(code_hash="", code_expires_at=None, verified_at=now)
    signer.verified_at = now
    return True


def mark_verified(session, signer: Signer) -> None:
    verified = dict(session.get(SESSION_KEY) or {})
    verified[str(signer.pk)] = (timezone.now() + VERIFIED_FOR).isoformat()
    session[SESSION_KEY] = verified


def is_verified(session, signer: Signer) -> bool:
    until = (session.get(SESSION_KEY) or {}).get(str(signer.pk))
    if not until:
        return False
    try:
        return datetime.datetime.fromisoformat(until) > timezone.now()
    except ValueError:
        return False


def _locked(signer: Signer) -> tuple[Signer, SigningRequest]:
    req = SigningRequest.objects.select_for_update(of=("self",)).select_related("organization", "lease").get(
        pk=signer.request_id)
    signer = Signer.objects.select_for_update().get(pk=signer.pk)
    signer.request = req
    if not req.is_open:
        raise ValidationError(_("This request is closed; nothing more can be signed."))
    if signer.has_signed or signer.declined_at:
        raise ValidationError(_("This has already been answered."))
    return signer, req


def _clean_signature(typed_name: str, agreed: bool) -> str:
    typed_name = " ".join((typed_name or "").split())[:200]
    if len(typed_name) < 2:
        raise ValidationError({"typed_name": _("Type your full name.")})
    if not agreed:
        raise ValidationError({"agree": _("Tick the box to agree to sign electronically.")})
    return typed_name


def _sign(signer: Signer, typed_name: str, request, user=None) -> None:
    from core.net import client_ip

    signer.signed_at = timezone.now()
    signer.signed_name = typed_name
    signer.signed_by = user
    signer.signed_ip = (client_ip(request) or None) if request is not None else None
    signer.signed_user_agent = request.META.get("HTTP_USER_AGENT", "")[:300] if request is not None else ""
    signer.save(update_fields=["signed_at", "signed_name", "signed_by", "signed_ip", "signed_user_agent",
                               "updated_at"])


@transaction.atomic
def sign_as_tenant(signer: Signer, *, typed_name: str, agreed: bool, request=None) -> SigningRequest:
    typed_name = _clean_signature(typed_name, agreed)
    signer, req = _locked(signer)
    if signer.role != Signer.Role.TENANT or signer.verified_at is None:
        raise PermissionDenied
    _sign(signer, typed_name, request)
    _audit("esign.sign", req, request, changes={"signer": [None, signer.name], "role": [None, signer.role],
                                                "typed_name": [None, typed_name]})
    return _complete_if_done(req, request)


@transaction.atomic
def sign_as_landlord(actor: Membership, req: SigningRequest, *, typed_name: str, agreed: bool,
                     request=None) -> SigningRequest:
    _staff_request(actor, req)
    typed_name = _clean_signature(typed_name, agreed)
    landlord = req.signers.get(role=Signer.Role.LANDLORD)
    signer, req = _locked(landlord)
    _sign(signer, typed_name, request, user=actor.user)
    _audit("esign.sign", req, request, actor=actor.user, changes={
        "signer": [None, signer.name], "role": [None, signer.role], "typed_name": [None, typed_name]})
    return _complete_if_done(req, request)


@transaction.atomic
def decline(signer: Signer, reason: str, *, request=None) -> SigningRequest:
    reason = " ".join((reason or "").split())[:300]
    if not reason:
        raise ValidationError({"reason": _("Say why you are not signing, so the office can follow up.")})
    signer, req = _locked(signer)
    if signer.verified_at is None:
        raise PermissionDenied
    now = timezone.now()
    signer.declined_at, signer.decline_reason = now, reason
    signer.save(update_fields=["declined_at", "decline_reason", "updated_at"])
    req.status, req.closed_at, req.close_reason = Status.DECLINED, now, reason
    req.save(update_fields=["status", "closed_at", "close_reason", "updated_at"])
    _audit("esign.decline", req, request, changes={"signer": [None, signer.name], "reason": [None, reason]})
    _tell_sender(req, "esign_declined", {"signer": signer.name, "reason": reason})
    return req


def _tell_sender(req: SigningRequest, codename: str, extra: dict) -> None:
    from notifications.delivery import notify

    notify(req.organization, codename, user=req.created_by, lease=req.lease,
           context={"number": req.number, "title": req.title, **extra})


def _complete_if_done(req: SigningRequest, request) -> SigningRequest:
    signers = list(req.signers.select_related("signed_by").order_by("position", "pk"))
    if any(not s.has_signed for s in signers):
        return req
    req.completed_at = timezone.now()
    original = req.original_pdf.open("rb").read()
    req.original_pdf.close()
    if _sha256(original) != req.original_sha256:
        raise RuntimeError(f"The stored PDF of {req.number} no longer matches its fingerprint.")
    signed = pdf.join(original, pdf.render_record(req, signers, check_url(req)))
    req.signed_sha256 = _sha256(signed)
    req.signed_pdf.save(f"{uuid.uuid4().hex}.pdf", ContentFile(signed), save=False)
    req.document = _file_copy(req, signed)
    req.status = Status.COMPLETED
    req.save(update_fields=["status", "completed_at", "signed_sha256", "signed_pdf", "document", "updated_at"])
    Signer.objects.filter(request=req).update(code_hash="")
    _audit("esign.complete", req, request, actor=req.created_by, changes={"sha256": [None, req.signed_sha256]})
    _tell_sender(req, "esign_completed", {})
    return req


def _file_copy(req: SigningRequest, content: bytes) -> Document:
    """Files the signed copy on the lease. Made by the system, in the name of whoever sent it for signing."""
    from documents.services import place_fields

    lease = req.lease
    category = Document.Category.LEASE if req.kind == Kind.AGREEMENT else Document.Category.NOTICE
    filename = f"{req.number}-signed.pdf"
    doc = Document(organization=req.organization, **place_fields(lease), title=(req.title + " (" + _("signed")
                   + ")")[:120], category=category, kind=Document.Kind.PDF, filename=filename, size=len(content),
                   uploaded_by=req.created_by, shared_with_tenant=req.share_with_tenant,
                   note=_("Signed electronically, %(number)s.") % {"number": req.number})
    doc.file.save(f"{secrets.token_hex(16)}.pdf", ContentFile(content), save=False)
    doc.save()
    audit.record("documents.upload", actor=req.created_by, organization=req.organization, obj=doc, changes={
        "place": [None, doc.place], "title": [None, doc.title], "file": [None, doc.filename],
        "source": [None, req.number]})
    return doc


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def _signer_row(s: Signer) -> dict:
    when = lambda moment: moment.isoformat() if moment else None  # noqa: E731
    return {"role": s.role, "name": s.name, "phone": s.phone, "link_sent_at": when(s.link_sent_at),
            "code_entered_at": when(s.verified_at), "signed_at": when(s.signed_at), "signed_name": s.signed_name,
            "signed_by": s.signed_by.get_full_name() if s.signed_by_id else None, "ip": s.signed_ip,
            "browser": s.signed_user_agent, "declined_at": when(s.declined_at), "decline_reason": s.decline_reason}


def evidence_pack(actor: Membership, req: SigningRequest, *, request=None) -> bytes:
    """A ZIP for a court or the tribunal: the files with their fingerprints, the signers, the request's audit
    trail, and a certificate under the Evidence Act s.106B for a responsible person to fill in and sign."""
    _staff_request(actor, req)
    files = {f"{req.number}.pdf": req.original_pdf.open("rb").read()}
    req.original_pdf.close()
    if req.signed_pdf:
        files[f"{req.number}-signed.pdf"] = req.signed_pdf.open("rb").read()
        req.signed_pdf.close()
    signers = list(req.signers.select_related("signed_by").order_by("position", "pk"))
    events = list(AuditEvent.objects.filter(organization=req.organization, object_type="esign.signingrequest",
                                            object_id=str(req.public_id)).select_related("actor")
                  .order_by("created_at", "pk"))
    record = {
        "number": req.number, "title": req.title, "kind": req.kind, "status": req.status,
        "lease": str(req.lease), "property": req.lease.unit.property.name, "unit": req.lease.unit.code,
        "organization": req.organization.name, "sent_at": req.created_at.isoformat(),
        "completed_at": req.completed_at.isoformat() if req.completed_at else None,
        "no_renewal_option_confirmed": req.no_renewal_option, "check_url": check_url(req),
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
        "signers": [_signer_row(s) for s in signers],
        "audit": [{"at": e.created_at.isoformat(), "action": e.action,
                   "by": e.actor.get_full_name() if e.actor_id else None, "ip": e.ip, "browser": e.user_agent,
                   "changes": e.changes} for e in events],
    }
    certificate = pdf.render_certificate(req, record, keep_until(req))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as pack:
        for name, content in files.items():
            pack.writestr(name, content)
        pack.writestr("record.json", json.dumps(record, indent=2, ensure_ascii=False, default=str))
        pack.writestr("certificate.pdf", certificate)
    _audit("esign.evidence", req, request, actor=actor.user)
    return out.getvalue()
