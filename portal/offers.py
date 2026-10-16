"""Renewal offers (D-131): the office offers, the tenant answers in the portal, the office starts the draft.

Accepting changes nothing on the lease. It tells the office, who start the renewal draft from the offer and
activate it in the usual way.
"""

import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import require
from audit import services as audit
from leases import services as lease_services
from leases.models import Lease
from notifications import triggers

from . import selectors
from .models import RenewalOffer

DAY = datetime.timedelta(days=1)
DEFAULT_DAYS = 14


def latest(lease: Lease) -> RenewalOffer | None:
    return RenewalOffer.objects.filter(lease=lease).first()


def open_offer(lease: Lease) -> RenewalOffer | None:
    """The offer the tenant can still answer."""
    offer = RenewalOffer.objects.filter(lease=lease, status=RenewalOffer.Status.OFFERED).first()
    return offer if offer is not None and offer.is_open else None


def _changes(offer: RenewalOffer) -> dict:
    end = str(offer.end_date) if offer.end_date else None
    return {"start_date": [None, str(offer.start_date)], "end_date": [None, end],
            "rent": [None, str(offer.rent)], "expires_on": [None, str(offer.expires_on)]}


@transaction.atomic
def make_offer(actor: Membership, lease: Lease, *, start_date: datetime.date | None, end_date: datetime.date | None,
               rent, note: str = "", expires_on: datetime.date | None = None, request=None) -> RenewalOffer:
    if lease.organization_id != actor.organization_id:
        raise PermissionDenied("organization")
    require(actor, "leases.draft", lease.unit.property)
    lease = Lease.all_objects.select_for_update().get(pk=lease.pk)
    if lease.status != Lease.Status.ACTIVE or lease.archived_at is not None:
        raise ValidationError(_("Only an active lease can be offered a renewal."))
    if lease_services.open_notice(lease) is not None:
        raise ValidationError(_("This tenant has given notice to vacate. Withdraw the notice first."))
    if lease_services.open_successor(lease) is not None:
        raise ValidationError(_("A draft renewal or transfer of this lease already exists."))
    if open_offer(lease) is not None:
        raise ValidationError(_("An offer is already open. Withdraw it first to make a new one."))
    today = timezone.localdate()
    if start_date is None:
        if lease.end_date is None:
            raise ValidationError({"start_date": _("This lease has no end date. Choose when the renewal starts.")})
        start_date = lease.end_date + DAY
    if start_date <= lease.start_date:
        raise ValidationError({"start_date": _("The renewal must start after this lease started.")})
    if end_date is not None and end_date < start_date:
        raise ValidationError({"end_date": _("The renewal cannot end before it starts.")})
    try:
        rent = Decimal(rent)
    except (InvalidOperation, TypeError):
        raise ValidationError({"rent": _("Enter the monthly rent.")}) from None
    if rent <= 0:
        raise ValidationError({"rent": _("Enter the monthly rent.")})
    expires_on = expires_on or today + datetime.timedelta(days=DEFAULT_DAYS)
    if expires_on < today:
        raise ValidationError({"expires_on": _("The answer date cannot be in the past.")})
    offer = RenewalOffer.objects.create(organization=lease.organization, lease=lease, start_date=start_date,
                                        end_date=end_date, rent=rent, note=note.strip()[:500], expires_on=expires_on,
                                        offered_by=actor.user)
    audit.record("lease.renewal_offered", actor=actor.user, organization=lease.organization, obj=lease,
                 request=request, changes=_changes(offer))
    transaction.on_commit(lambda: triggers.renewal_offered(offer))
    return offer


@transaction.atomic
def withdraw(actor: Membership, offer: RenewalOffer, *, request=None) -> RenewalOffer:
    if offer.organization_id != actor.organization_id:
        raise PermissionDenied("organization")
    require(actor, "leases.draft", offer.lease.unit.property)
    offer = RenewalOffer.objects.select_for_update().get(pk=offer.pk)
    if offer.status != RenewalOffer.Status.OFFERED:
        raise ValidationError(_("This offer has already been answered."))
    offer.status = RenewalOffer.Status.WITHDRAWN
    offer.save(update_fields=["status", "updated_at"])
    audit.record("lease.renewal_withdrawn", actor=actor.user, organization=offer.organization, obj=offer.lease,
                 request=request, changes={"status": ["OFFERED", "WITHDRAWN"]})
    return offer


@transaction.atomic
def answer(user, offer: RenewalOffer, *, accept: bool, request=None) -> RenewalOffer:
    """A tenant on the lease accepts or declines. Anyone else gets a 404 from the view."""
    if offer.lease_id not in selectors.own_lease_ids(user):
        raise PermissionDenied("portal")
    account = (selectors.live_accounts(user).filter(tenant__lease_links__lease_id=offer.lease_id)
               .select_related("tenant").first())
    if account is None:
        raise PermissionDenied("portal")
    offer = RenewalOffer.objects.select_for_update().get(pk=offer.pk)
    if offer.status != RenewalOffer.Status.OFFERED:
        raise ValidationError(_("This offer has already been answered or withdrawn."))
    if not offer.is_open:
        raise ValidationError(_("This offer has expired. Call the office."))
    offer.status = RenewalOffer.Status.ACCEPTED if accept else RenewalOffer.Status.DECLINED
    offer.responded_at = timezone.now()
    offer.responded_by = user
    offer.responded_by_name = account.tenant.name
    offer.save(update_fields=["status", "responded_at", "responded_by", "responded_by_name", "updated_at"])
    audit.record("lease.renewal_accepted" if accept else "lease.renewal_declined", actor=user,
                 organization=offer.organization, obj=offer.lease, request=request,
                 changes={"status": ["OFFERED", offer.status], "tenant": [None, account.tenant.name]})
    transaction.on_commit(lambda: triggers.renewal_answered(offer))
    return offer


@transaction.atomic
def start_draft(actor: Membership, offer: RenewalOffer, *, request=None) -> Lease:
    """The renewal draft for an accepted offer, linked back to the offer."""
    offer = RenewalOffer.objects.select_for_update().get(pk=offer.pk)
    if offer.status != RenewalOffer.Status.ACCEPTED or offer.successor_id is not None:
        raise ValidationError(_("There is no accepted offer to start a draft from."))
    draft = lease_services.renew_lease(actor, offer.lease, start_date=offer.start_date, end_date=offer.end_date,
                                       rent=offer.rent, request=request)
    offer.successor = draft
    offer.save(update_fields=["successor", "updated_at"])
    return draft

