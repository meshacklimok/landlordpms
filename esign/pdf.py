"""The signing record page, and the signed copy: the original pages with the record after them (D-118)."""

import io

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext as _
from pypdf import PdfReader, PdfWriter
from reportlab.lib.units import mm
from reportlab.platypus import Spacer

from core.phone import mask_phone
from leases.pdf import BLANK, HEAD, SECTION, SMALL, TITLE, _doc, _facts, _p


def _when(moment) -> str:
    return f"{timezone.localtime(moment):%d %b %Y %H:%M:%S %Z}" if moment else ""


def render_record(req, signers, check_url: str) -> bytes:
    buf = io.BytesIO()
    doc = _doc(buf, f"{_('Signing record')} {req.number}")
    story = [_p(req.organization.name, HEAD), Spacer(1, 6 * mm), _p(_("SIGNING RECORD"), TITLE), Spacer(1, 3 * mm),
             _facts([
                 (_("Document"), req.title),
                 (_("Signing number"), req.number),
                 (_("Pages signed"), _("All pages before this record")),
                 (_("Sent for signing"), _when(req.created_at)),
                 (_("Signed by everyone"), _when(req.completed_at)),
                 (_("Fingerprint (SHA-256) of the pages signed"), req.original_sha256),
             ] + ([(_("Term"), _("Confirmed by the sender: two years or less with no option to renew, or "
                                    "periodic"))] if req.no_renewal_option else []))]
    for signer in signers:
        story.append(_p(f"{signer.get_role_display()}: {signer.name}", SECTION))
        if signer.role == signer.Role.TENANT:
            how = _("Opened a private link sent to them, then entered a one-time code sent by SMS to %(phone)s "
                    "at %(when)s.") % {"phone": mask_phone(signer.phone), "when": _when(signer.verified_at)}
        else:
            how = _("Signed in to %(org)s's account on NyumbaPMS as %(user)s.") % {
                "org": req.organization.name, "user": signer.signed_by.get_full_name() or signer.signed_by.phone}
        story.append(_facts([
            (_("Name typed"), signer.signed_name),
            (_("Signed at"), _when(signer.signed_at)),
            (_("How they were checked"), how),
            (_("IP address"), signer.signed_ip or ""),
            (_("Browser"), signer.signed_user_agent[:150]),
        ]))
    story += [Spacer(1, 6 * mm), _p(_(
        "Each signer typed their name and agreed to sign electronically, and that doing so has the same effect as "
        "signing on paper. To check a copy, open %(url)s : it shows this record and the fingerprints. A copy whose "
        "SHA-256 differs from the one shown there has been changed.") % {"url": check_url}, SMALL)]
    doc.build(story)
    return buf.getvalue()


def render_certificate(req, record: dict, keep_until) -> bytes:
    """A certificate for the evidence pack, following the Evidence Act (Cap. 80) s.106B(4): it identifies the
    records, says how they were produced and by what, and covers the conditions in s.106B(2). A person in a
    responsible position fills in the blanks and signs it."""
    buf = io.BytesIO()
    doc = _doc(buf, f"{_('Certificate')} {req.number}")
    story = [
        _p(req.organization.name, HEAD), Spacer(1, 6 * mm),
        _p(_("CERTIFICATE UNDER SECTION 106B(4) OF THE EVIDENCE ACT (CAP. 80)"), TITLE), Spacer(1, 3 * mm),
        _p(_("1. The records. This certificate concerns the electronic records below, about %(title)s "
             "(signing number %(number)s), together with record.json, which lists the signers and every step "
             "taken on the request.") % {"title": req.title, "number": req.number}),
        Spacer(1, 2 * mm),
        _facts([(name, _("SHA-256 %(sha)s") % {"sha": sha}) for name, sha in record["files"].items()]),
        Spacer(1, 3 * mm),
        _p(_("2. How they were produced. The records were produced by NyumbaPMS, a web application run for "
             "%(org)s at %(site)s. When the document was sent for signing (%(sent)s) the system stored the PDF "
             "and its SHA-256 fingerprint, and did not change it afterwards. Each tenant opened a private link sent "
             "by SMS and entered a one-time code sent by SMS to the phone number on file before signing. Each "
             "signer typed their name and ticked that they agreed to sign electronically; the system recorded the "
             "time, IP address and browser. When everyone had signed, the system added a signing record page "
             "after the original pages and stored the fingerprint of that copy. Each step was written to an audit "
             "trail that the application cannot edit or delete.") % {
                 "org": req.organization.name, "site": settings.SITE_URL, "sent": _when(req.created_at)}),
        Spacer(1, 3 * mm),
        _p(_("3. Conditions. The records were produced in the ordinary course of %(org)s's management of the "
             "tenancy, during a period when the system was used regularly to store and process such information. "
             "Throughout that period information of this kind was regularly supplied to the system in the "
             "ordinary course of those activities. To the best of my knowledge the system was operating properly "
             "throughout; if at any time it was not, that did not affect the production of these records or "
             "their accuracy. The records reproduce the information as it was supplied to the system.") % {
                 "org": req.organization.name}),
        Spacer(1, 3 * mm),
        _p(_("4. Checking a copy. The fingerprints can be checked at %(url)s . A copy whose SHA-256 differs from "
             "the one shown there has been changed.") % {"url": record["check_url"]}),
        Spacer(1, 3 * mm),
        _p(_("To the best of my knowledge and belief, the matters stated in this certificate are true.")),
        Spacer(1, 8 * mm),
        _facts([
            (_("Name"), BLANK * 2),
            (_("Position (in relation to the system or the management of the tenancy)"), BLANK * 2),
            (_("Signature"), BLANK * 2),
            (_("Date"), BLANK * 2),
        ]),
        Spacer(1, 6 * mm),
        _p(_("Keep these records until %(day)s.") % {"day": keep_until.strftime("%d %b %Y")} if keep_until else
           _("Keep these records until six years after the lease ends."), SMALL),
    ]
    doc.build(story)
    return buf.getvalue()


def join(original: bytes, record: bytes) -> bytes:
    """The original pages unchanged, then the record."""
    writer = PdfWriter()
    for part in (original, record):
        for page in PdfReader(io.BytesIO(part)).pages:
            writer.add_page(page)
    title = (PdfReader(io.BytesIO(original)).metadata or {}).get("/Title")
    if title:
        writer.add_metadata({"/Title": str(title)})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
