"""Demo requests from the public form (D-133)."""

import datetime
import logging

from django.conf import settings
from django.core.mail import send_mail

from notifications.email_html import send_branded
from django.utils import timezone
from django.utils.translation import gettext as _

from core.net import client_ip
from notifications.providers.mail import SYSTEM, mailer_for
from ops import alerts

from . import campaigns
from .models import DemoRequest

logger = logging.getLogger(__name__)

PER_IP_HOUR = 5


def book_demo(data: dict, request=None) -> tuple[DemoRequest, bool]:
    """Saves the request. The same phone asking again within a day updates its open request instead of adding a
    second one, so a nervous double tap does not make the team call twice. Returns (request, created)."""
    tags = campaigns.seen(request)
    fields = {k: data.get(k, "") for k in (
        "full_name", "email", "organization_name", "kind", "size", "channel", "preferred_date", "preferred_slot",
        "message")}
    since = timezone.now() - datetime.timedelta(hours=24)
    existing = DemoRequest.objects.filter(phone=data["phone"], status=DemoRequest.Status.NEW,
                                          created_at__gte=since).first()
    if existing is not None:
        for key, value in fields.items():
            setattr(existing, key, value)
        existing.save()
        return existing, False
    item = DemoRequest.objects.create(phone=data["phone"], source=tags.get("source", "")[:60],
                                      campaign=tags.get("campaign", "")[:60], gclid=tags.get("gclid", ""),
                                      fbclid=tags.get("fbclid", ""), **fields)
    notify_team(item)
    send_confirmation(item)
    return item, True


def _when(item: DemoRequest) -> str:
    return f"{item.preferred_date:%A %d %B %Y}, {item.get_preferred_slot_display()}"


def notify_team(item: DemoRequest) -> None:
    to = alerts.recipients()
    if not to:
        return
    body = "\n".join([
        f"{item.full_name} ({item.phone}) asked for a demo.",
        f"Manages: {item.get_kind_display()}; {item.get_size_display()}"
        + (f"; {item.organization_name}" if item.organization_name else ""),
        f"Wants: {item.get_channel_display()}, {_when(item)}",
        f"Email: {item.email or 'none'}",
        f"Came from: {item.source or 'direct'}"
        + (f", campaign {item.campaign}" if item.campaign else "")
        + (" (Google ad click)" if item.gclid else "") + (" (Meta ad click)" if item.fbclid else ""),
        f"Note: {item.message or 'none'}",
        "",
        f"{settings.SITE_URL}/ok-ml-staff/growth/demorequest/",
    ])
    try:
        send_mail(f"Demo request: {item.full_name}", body, settings.DEFAULT_FROM_EMAIL, to, fail_silently=False,
                  using=mailer_for(SYSTEM))
    except Exception:  # the request is saved either way; the admin list is the source of truth
        logger.exception("Could not email the team about demo request %s", item.pk)


def send_confirmation(item: DemoRequest) -> None:
    if not item.email:
        return
    body = _("Hello %(name)s,\n\nThank you for asking for a NyumbaPMS demo. We will contact you on %(phone)s to "
             "agree a time. You asked for %(when)s.\n\nUntil then you can read the tutorials at %(site)s/tutorials/.\n") % {
        "name": item.full_name, "phone": item.phone, "when": _when(item), "site": settings.SITE_URL}
    try:
        send_branded(_("We received your demo request"), body, [item.email], using=mailer_for(SYSTEM),
                     from_email=settings.DEFAULT_FROM_EMAIL, button_label=_("Read the tutorials"))
    except Exception:
        logger.exception("Could not email the confirmation for demo request %s", item.pk)


def ip_key(request) -> str:
    return f"demo:{client_ip(request)}"
