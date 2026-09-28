"""Read-side queries for the message log and the bell."""

from django.db.models import Count, Q
from django.urls import reverse

from accounts.models import Membership

from .models import Message

Status = Message.Status

# Tabs on the log, in order: key → statuses.
STATES = {
    "waiting": (Status.QUEUED,),
    "sent": (Status.SENT, Status.DELIVERED),
    "failed": (Status.FAILED,),
    "skipped": (Status.SKIPPED,),
}


def visible_messages(membership: Membership):
    """Messages to tenants this member may see, and (organization-wide members only) to bare numbers.

    Staff in-app messages belong to the bell, not the log.
    """
    from tenants.models import Tenant
    from tenants.services import visible_tenants

    qs = Message.objects.for_org(membership.organization).filter(user__isnull=True)
    if not membership.all_properties:
        qs = qs.filter(tenant__in=visible_tenants(membership, Tenant.all_objects.all()))
    return qs


def counts_by_state(qs) -> dict[str, int]:
    agg = qs.aggregate(all=Count("pk"), **{key: Count("pk", filter=Q(status__in=statuses))
                                           for key, statuses in STATES.items()})
    return agg


def link_for(message: Message) -> str:
    """Where an in-app message points: the payment, invoice or lease it is about."""
    if message.payment_id:
        return reverse("payments:detail", args=[message.payment.public_id])
    if message.invoice_id:
        return reverse("billing:invoice", args=[message.invoice.public_id])
    if message.lease_id:
        return reverse("leases:detail", args=[message.lease.public_id])
    return ""


def visible_tenant(membership: Membership, public_id):
    """The tenant with this id if the member can see it, else None."""
    from tenants.models import Tenant
    from tenants.services import visible_tenants

    if public_id is None:
        return None
    return visible_tenants(membership, Tenant.all_objects.all()).filter(public_id=public_id).first()


def recent_for_tenant(tenant, limit: int = 5):
    return Message.objects.for_org(tenant.organization).filter(tenant=tenant).order_by("-created_at", "-pk")[:limit]
