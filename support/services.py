"""Support requests (D-063 item 3)."""

import logging
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from audit import services as audit
from subscriptions.services import next_platform_number

from .models import SupportRequest

logger = logging.getLogger(__name__)

MAX_ATTACHMENT = 10 * 1024 * 1024
ALLOWED_SUFFIXES = {".csv", ".xlsx", ".xls", ".ods", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".txt"}


def check_attachment(upload) -> None:
    if upload is None:
        return
    if upload.size > MAX_ATTACHMENT:
        raise ValidationError(_("The file is larger than 10 MB."))
    if Path(upload.name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValidationError(_("Attach a spreadsheet, CSV, PDF, text file or picture."))


@transaction.atomic
def create_request(user, organization, *, kind: str, subject: str, message: str, attachment=None,
                   page: str = "", user_agent: str = "", request=None) -> SupportRequest:
    check_attachment(attachment)
    item = SupportRequest(
        number=next_platform_number("support", "SUP", timezone.localdate().year), organization=organization,
        created_by=user, kind=kind, subject=subject.strip()[:150], message=message.strip(),
        page=(page or "")[:300], user_agent=(user_agent or "")[:300])
    if attachment is not None:
        item.attachment_name = Path(attachment.name).name[:200]
        item.attachment.save(attachment.name, attachment, save=False)
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
        send_mail(f"[{item.number}] {item.subject}", body, None, [to])
    except Exception:
        # The request is saved and shows in admin; a mail outage must not lose it.
        logger.exception("Could not email support request %s", item.number)
