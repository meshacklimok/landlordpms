"""Support requests (D-063 item 3)."""

import logging
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from audit import services as audit
from inspections import photos
from notifications.providers.mail import SYSTEM, mailer_for
from notifications.providers.sms import send_sms
from subscriptions.services import next_platform_number

from .models import SupportReply, SupportRequest

logger = logging.getLogger(__name__)

MAX_ATTACHMENT = 10 * 1024 * 1024
PHOTOS = {".png", ".jpg", ".jpeg", ".webp"}
ZIPS = {".xlsx", ".ods"}
TEXT = {".csv", ".txt"}
ALLOWED_SUFFIXES = PHOTOS | ZIPS | TEXT | {".xls", ".pdf"}
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # the start of every old Office file, such as .xls


def _is_zip(upload) -> bool:
    try:
        with zipfile.ZipFile(upload) as archive:
            return bool(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return False
    finally:
        upload.seek(0)


def process_attachment(upload) -> tuple[File, str]:
    """Returns (file to store, its name) once the contents match the name (D-108 item 4), or raises
    ValidationError. Pictures are saved again as JPEG, which also drops EXIF data such as the location."""
    if upload.size > MAX_ATTACHMENT:
        raise ValidationError(_("The file is larger than 10 MB."))
    upload.seek(0)  # the form has already read it once
    name = Path(upload.name).name[:200]
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValidationError(_("Attach a spreadsheet, CSV, PDF, text file or picture."))
    if suffix in PHOTOS:
        try:
            image, _w, _h = photos.process(upload)
        except ValidationError:
            raise ValidationError(_("That picture cannot be read. Use JPEG or PNG.")) from None
        return image, f"{Path(name).stem[:190]}.jpg"
    head = upload.read(8)
    upload.seek(0)
    if suffix == ".pdf":
        ok = head.startswith(b"%PDF-")
    elif suffix == ".xls":
        ok = head == OLE
    elif suffix in ZIPS:
        ok = head.startswith(b"PK\x03\x04") and _is_zip(upload)
    else:
        ok = b"\x00" not in upload.read(MAX_ATTACHMENT)
        upload.seek(0)
    if not ok:
        raise ValidationError(_("That file is not the kind its name says (%(suffix)s).") % {"suffix": suffix})
    return upload, name


def check_attachment(upload) -> None:
    if upload is not None:
        process_attachment(upload)


@transaction.atomic
def create_request(user, organization, *, kind: str, subject: str, message: str, attachment=None,
                   page: str = "", user_agent: str = "", request=None) -> SupportRequest:
    if attachment is not None:
        attachment, attachment_name = process_attachment(attachment)
    item = SupportRequest(
        number=next_platform_number("support", "SUP", timezone.localdate().year), organization=organization,
        created_by=user, kind=kind, subject=subject.strip()[:150], message=message.strip(),
        page=(page or "")[:300], user_agent=(user_agent or "")[:300])
    if attachment is not None:
        item.attachment_name = attachment_name
        item.attachment.save(attachment_name, attachment, save=False)
    item.save()
    audit.record("support_request.create", actor=user, organization=organization, obj=item, request=request,
                 changes={"number": item.number, "kind": kind})
    transaction.on_commit(lambda: _email(item))
    return item


def _email(item: SupportRequest) -> None:
    to = getattr(settings, "SUPPORT_EMAIL", "")
    if not to:
        return
    org = item.organization.name if item.organization else "-"
    body = (f"{item.get_kind_display()} from {item.created_by.full_name or item.created_by.phone} "
            f"({item.created_by.phone}), {org}\nPage: {item.page or '-'}\n"
            f"Attachment: {item.attachment_name or '-'}\n\n{item.message}\n\nOpen it in admin.")
    try:
        send_mail(f"[{item.number}] {item.subject}", body, None, [to], using=mailer_for(SYSTEM))
    except Exception:
        # The request is saved and shows in admin; a mail outage must not lose it.
        logger.exception("Could not email support request %s", item.number)


def reply(actor, item: SupportRequest, body: str, request=None) -> SupportReply:
    """Answers the landlord who sent `item`: by email when they have one, otherwise by SMS. The first answer sets
    `first_reply_at`, and the request now waits on them. A send that fails is kept (sent=False) so nothing is lost."""
    body = (body or "").strip()[:2000]
    if not body:
        raise ValidationError(_("Write something to send."))
    person = item.created_by
    channel = SupportReply.Channel.EMAIL if person.email else SupportReply.Channel.SMS
    sent = False
    try:
        if channel == SupportReply.Channel.EMAIL:
            sent = bool(send_mail(f"Re: [{item.number}] {item.subject}", body, None, [person.email],
                                  using=mailer_for(SYSTEM)))
        else:
            sent = send_sms(person.phone, f"NyumbaPMS support ({item.number}): {body}"[:300]).ok
    except Exception:
        logger.exception("Could not send the reply to support request %s", item.number)
    row = SupportReply.objects.create(request=item, sent_by=actor, channel=channel, body=body, sent=sent)
    if sent:
        item.first_reply_at = item.first_reply_at or row.created_at
        if item.status == SupportRequest.Status.OPEN:
            item.status = SupportRequest.Status.WAITING
        item.save(update_fields=["first_reply_at", "status", "updated_at"])
    audit.record("console.support_reply", actor=actor, organization=item.organization, obj=item, request=request,
                 changes={"channel": [None, channel], "sent": [None, sent]})
    return row
