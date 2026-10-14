"""The thread between a lease's tenants and the office, and what the tenant has been sent (D-131).

One thread per lease. A tenant writes from the portal; the office replies from Conversations. Each side is told
(staff in the app, the tenant by SMS) and sees what is unread.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Max, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import can, require
from core import ratelimit
from leases.models import Lease
from leases.services import visible_leases
from notifications import triggers
from notifications.models import Message

from . import selectors
from .models import PortalMessage

# A tenant can write this many notes an hour on one lease.
PER_HOUR = 10
SHOWN = 100
RECEIVED_SHOWN = 30


def _clean(body: str) -> str:
    body = (body or "").strip()
    if not body:
        raise ValidationError({"body": _("Write your message first.")})
    if len(body) > PortalMessage.MAX_LENGTH:
        raise ValidationError({"body": _("Keep it under %(n)s characters.") % {"n": PortalMessage.MAX_LENGTH}})
    return body


def thread(lease: Lease) -> list[PortalMessage]:
    """The newest notes, oldest first."""
    latest = list(PortalMessage.objects.filter(lease=lease).select_related("author", "tenant")
                  .order_by("-created_at", "-pk")[:SHOWN])
    return latest[::-1]


@transaction.atomic
def tenant_send(user, lease: Lease, body: str) -> PortalMessage:
    account = selectors.live_accounts(user).filter(tenant__lease_links__lease=lease).select_related("tenant").first()
    if account is None or lease.pk not in selectors.own_lease_ids(user):
        raise PermissionDenied("portal")
    if lease.status != Lease.Status.ACTIVE:
        raise ValidationError(_("You can write to the office while your lease is active. Call them instead."))
    body = _clean(body)
    if not ratelimit.hit(f"portal_message:{lease.pk}", PER_HOUR, 3600):
        raise ValidationError(_("You have sent several messages just now. Wait a little, or call the office."))
    message = PortalMessage.objects.create(organization=lease.organization, lease=lease, tenant=account.tenant,
                                           side=PortalMessage.Side.TENANT, author=user, body=body)
    transaction.on_commit(lambda: triggers.portal_message_received(message))
    return message


@transaction.atomic
def staff_reply(actor: Membership, lease: Lease, body: str) -> PortalMessage:
    if lease.organization_id != actor.organization_id:
        raise PermissionDenied("organization")
    require(actor, "messages.send", lease.unit.property)
    lead = lease.primary_tenant or next(iter(lease.lease_tenants.select_related("tenant")), None)
    lead = getattr(lead, "tenant", lead)
    if lead is None:
        raise ValidationError(_("This lease has no tenant to write to."))
    body = _clean(body)
    message = PortalMessage.objects.create(organization=lease.organization, lease=lease, tenant=lead,
                                           side=PortalMessage.Side.STAFF, author=actor.user, body=body)
    transaction.on_commit(lambda: triggers.portal_message_reply(message))
    return message


def mark_read_by_tenant(lease: Lease) -> int:
    return PortalMessage.objects.filter(lease=lease, side=PortalMessage.Side.STAFF, read_at__isnull=True
                                        ).update(read_at=timezone.now())


def mark_read_by_staff(lease: Lease) -> int:
    return PortalMessage.objects.filter(lease=lease, side=PortalMessage.Side.TENANT, read_at__isnull=True
                                        ).update(read_at=timezone.now())


def unread_for_tenant(user) -> dict[int, int]:
    """{lease id: replies the tenant has not opened}."""
    rows = (PortalMessage.objects.filter(lease_id__in=selectors.own_lease_ids(user), side=PortalMessage.Side.STAFF,
                                         read_at__isnull=True).values("lease_id").annotate(n=Count("pk")))
    return {row["lease_id"]: row["n"] for row in rows}


def received(lease: Lease) -> list[Message]:
    """Announcements and messages the office wrote to this lease's tenants, newest first (not rent reminders)."""
    tenant_ids = list(lease.lease_tenants.values_list("tenant_id", flat=True))
    return list(Message.objects.filter(organization=lease.organization, tenant_id__in=tenant_ids,
                                       status__in=[Message.Status.SENT, Message.Status.DELIVERED])
                .filter(Q(announcement__isnull=False) | Q(type="tenant_message"))
                .exclude(body="").order_by("-created_at", "-pk")[:RECEIVED_SHOWN])


# The office's side ----------------------------------------------------------------------------------


def staff_threads(membership: Membership) -> list[dict]:
    """The leases with notes in them, the ones waiting for a reply first: [{lease, last, unread}]."""
    can_see = can(membership, "messages.view") or can(membership, "messages.send")
    if not can_see:
        return []
    rows = (PortalMessage.objects.filter(organization=membership.organization)
            .values("lease_id").annotate(last=Max("created_at"),
                                         unread=Count("pk", filter=Q(side=PortalMessage.Side.TENANT,
                                                                     read_at__isnull=True))))
    stats = {row["lease_id"]: row for row in rows}
    leases = (visible_leases(membership, Lease.all_objects.all()).filter(pk__in=stats)
              .select_related("unit__property").prefetch_related("lease_tenants__tenant"))
    items = [{"lease": lease, "last": stats[lease.pk]["last"], "unread": stats[lease.pk]["unread"]} for lease in leases]
    return sorted(items, key=lambda item: (item["unread"] == 0, -item["last"].timestamp()))


def staff_unread(membership: Membership) -> int:
    """Tenant notes the staff member can reply to that nobody has opened; for the menu badge."""
    if not can(membership, "messages.send"):
        return 0
    leases = visible_leases(membership, Lease.all_objects.all()).values("pk")
    return PortalMessage.objects.filter(organization=membership.organization, lease_id__in=leases,
                                        side=PortalMessage.Side.TENANT, read_at__isnull=True).count()

