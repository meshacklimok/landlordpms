"""Prospects and viewings (D-072). Views stay thin; the rules live here."""

import datetime
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import can, require, visible_properties
from audit import services as audit
from core.phone import InvalidPhoneNumber, normalize_phone
from notifications.delivery import notify, staff_with
from properties.models import Property, Unit
from properties.selectors import is_available_to_let
from tenants import services as tenant_services
from tenants.models import Tenant

from .models import Prospect, ProspectNote, Viewing

Status = Prospect.Status
Kind = ProspectNote.Kind
STATS_DAYS = 90
# How far back a viewing may be recorded, and how far ahead booked.
VIEWING_PAST = datetime.timedelta(days=7)
VIEWING_AHEAD = datetime.timedelta(days=90)


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def visible_prospects(membership: Membership, queryset=None):
    """Prospects on the member's properties, with `prospects.view`."""
    qs = (queryset if queryset is not None else Prospect.objects.all()).for_org(membership.organization)
    if not can(membership, "prospects.view"):
        return qs.none()
    return qs.filter(property__in=visible_properties(membership, Property.all_objects.all()).values("pk"))


def sees(membership: Membership, p: Prospect) -> bool:
    return p.organization_id == membership.organization_id and can(membership, "prospects.view", p.property)


def can_manage(membership: Membership, p: Prospect) -> bool:
    return (sees(membership, p) and not p.property.is_archived
            and can(membership, "prospects.manage", p.property))


def can_convert(membership: Membership, p: Prospect) -> bool:
    return p.is_open and can_manage(membership, p) and can(membership, "tenants.manage")


def manageable_properties(membership: Membership) -> list[Property]:
    """Live properties where the member may add prospects."""
    return [p for p in visible_properties(membership, Property.objects.all()).order_by("name")
            if can(membership, "prospects.manage", p)]


def assignable_users(prop: Property) -> list:
    """Staff who may work on prospects at this property."""
    memberships = (Membership.objects.filter(organization_id=prop.organization_id, is_active=True,
                                             archived_at__isnull=True, user__is_active=True)
                   .select_related("user", "organization", "role").order_by("user__full_name", "user__pk"))
    return [m.user for m in memberships if can(m, "prospects.manage", prop)]


def prospect_staff(org) -> list:
    """Staff who work on prospects somewhere in the organization, for a form shown before a property is chosen."""
    memberships = (Membership.objects.filter(organization=org, is_active=True, archived_at__isnull=True,
                                             user__is_active=True)
                   .select_related("user", "organization", "role").order_by("user__full_name", "user__pk"))
    return [m.user for m in memberships if can(m, "prospects.manage")]


def _require(actor: Membership, p: Prospect) -> None:
    if not sees(actor, p):
        raise PermissionDenied("prospects.view")
    require(actor, "prospects.manage", p.property)
    if p.property.is_archived:
        raise ValidationError(_("This property is archived."))


def _require_open(p: Prospect) -> None:
    if not p.is_open:
        raise ValidationError(_("This prospect is closed. Reopen it first."))


def _audit(action, user, org, obj, request, changes=None):
    audit.record(action, actor=user, organization=org, obj=obj, request=request, changes=changes or {})


def _lock(p: Prospect) -> Prospect:
    return (Prospect.objects.select_for_update(of=("self",))
            .select_related("property", "unit", "organization", "assigned_to", "held_unit", "tenant")
            .get(pk=p.pk))


def _note(p: Prospect, kind: str, text: str, by, at=None) -> ProspectNote:
    return ProspectNote.objects.create(prospect=p, kind=kind, text=(text or "")[:1000], by=by,
                                       at=at or timezone.now())


def _when(moment: datetime.datetime) -> str:
    return timezone.localtime(moment).strftime("%d/%m/%Y %H:%M")


def _derive(p: Prospect) -> str:
    """The status of an open prospect follows what has happened (D-072 item 2)."""
    if p.held_unit_id:
        return Status.RESERVED
    viewings = set(p.viewings.values_list("status", flat=True))
    if Viewing.Status.BOOKED in viewings:
        return Status.VIEWING
    if Viewing.Status.DONE in viewings:
        return Status.VIEWED
    return Status.CONTACTED if p.contacted_at else Status.NEW


def _settle(p: Prospect) -> list | None:
    """Saves the derived status of an open prospect; returns [before, after] when it changed."""
    if not p.is_open:
        return None
    before, after = p.status, _derive(p)
    if before == after:
        return None
    p.status = after
    p.save(update_fields=["status", "updated_at"])
    return [before, after]


# ---------------------------------------------------------------------------
# Details
# ---------------------------------------------------------------------------


def _clean(prop: Property, *, name, phone, email="", source=Prospect.Source.WALK_IN, budget=None,
           move_in_by=None, wants="", unit=None, next_contact_on=None) -> tuple[dict, dict]:
    errors = {}
    name = " ".join((name or "").split())[:150]
    if not name:
        errors["name"] = _("Enter their name.")
    try:
        phone = normalize_phone(phone)
    except InvalidPhoneNumber as exc:
        errors["phone"] = str(exc)
    email = (email or "").strip()
    if email:
        try:
            validate_email(email)
        except ValidationError:
            errors["email"] = _("Enter a valid email address.")
    if source not in Prospect.Source.values:
        errors["source"] = _("Choose how they found us.")
    if budget not in (None, ""):
        try:
            budget = Decimal(str(budget)).quantize(Decimal("0.01"))
        except InvalidOperation:
            budget = None
            errors["budget"] = _("Enter an amount.")
        else:
            if budget < 0:
                errors["budget"] = _("Enter an amount.")
    else:
        budget = None
    if unit is not None and (unit.property_id != prop.pk or unit.is_archived):
        errors["unit"] = _("Choose a unit of this property.")
    return {"name": name, "phone": phone, "email": email, "source": source, "budget": budget,
            "move_in_by": move_in_by, "wants": (wants or "").strip()[:300], "unit": unit,
            "next_contact_on": next_contact_on}, errors


def _snapshot(p: Prospect) -> dict:
    return {"name": p.name, "phone": p.phone, "email": p.email, "source": p.source,
            "budget": str(p.budget) if p.budget is not None else None,
            "move_in_by": p.move_in_by.isoformat() if p.move_in_by else None, "wants": p.wants,
            "unit": p.unit.code if p.unit_id else None,
            "next_contact_on": p.next_contact_on.isoformat() if p.next_contact_on else None}


def open_with_phone(membership: Membership, phone: str, prop: Property | None = None, exclude_pk=None):
    """Open prospects the member can see with this phone, to warn before adding the same person twice."""
    qs = visible_prospects(membership).filter(phone=phone, status__in=Prospect.OPEN)
    if prop is not None:
        qs = qs.filter(property=prop)
    return qs.exclude(pk=exclude_pk) if exclude_pk else qs


@transaction.atomic
def create_prospect(actor: Membership, prop: Property, *, assigned_to=None, note: str = "", request=None,
                    **fields) -> Prospect:
    if prop.organization_id != actor.organization_id:
        raise PermissionDenied("prospects.manage")
    require(actor, "prospects.manage", prop)
    data, errors = _clean(prop, **fields)
    if prop.is_archived:
        errors["property"] = _("This property is archived.")
    if assigned_to is None:
        assigned_to = actor.user
    if assigned_to.pk not in {u.pk for u in assignable_users(prop)}:
        errors["assigned_to"] = _("That person cannot work on prospects at this property.")
    if errors:
        raise ValidationError(errors)
    p = Prospect.objects.create(organization=actor.organization, property=prop, assigned_to=assigned_to,
                                created_by=actor.user, **data)
    created = _note(p, Kind.CREATED, note, actor.user)
    _audit("prospects.create", actor.user, actor.organization, p, request,
           {"property": [None, prop.name], **{k: [None, v] for k, v in _snapshot(p).items() if v},
            "assigned_to": [None, str(assigned_to)]})
    if assigned_to.pk != actor.user_id:
        _notify_assigned(p, assigned_to, actor.user, created)
    return p


@transaction.atomic
def update_prospect(actor: Membership, p: Prospect, *, request=None, **fields) -> Prospect:
    p = _lock(p)
    _require(actor, p)
    data, errors = _clean(p.property, **fields)
    if errors:
        raise ValidationError(errors)
    before = _snapshot(p)
    for k, v in data.items():
        setattr(p, k, v)
    changes = audit.diff(before, _snapshot(p))
    if changes:
        p.save()
        _audit("prospects.edit", actor.user, actor.organization, p, request, changes)
    return p


@transaction.atomic
def assign(actor: Membership, p: Prospect, user, request=None) -> Prospect:
    p = _lock(p)
    _require(actor, p)
    _require_open(p)
    if user.pk not in {u.pk for u in assignable_users(p.property)}:
        raise ValidationError({"assigned_to": _("That person cannot work on prospects at this property.")})
    if user.pk == p.assigned_to_id:
        return p
    before = str(p.assigned_to) if p.assigned_to else None
    p.assigned_to = user
    p.save(update_fields=["assigned_to", "updated_at"])
    note = _note(p, Kind.ASSIGNED, _("Assigned to %(who)s") % {"who": user}, actor.user)
    _audit("prospects.assign", actor.user, actor.organization, p, request, {"assigned_to": [before, str(user)]})
    if user.pk != actor.user_id:
        _notify_assigned(p, user, actor.user, note)
    return p


@transaction.atomic
def add_note(actor: Membership, p: Prospect, *, text: str, contacted: bool = False, next_contact_on=None,
             now: datetime.datetime | None = None, request=None) -> ProspectNote:
    """A note, and whether they were reached. The follow-up date is set from the same form (blank clears it)."""
    p = _lock(p)
    _require(actor, p)
    text = (text or "").strip()[:1000]
    if not text:
        raise ValidationError({"text": _("Write what was said or agreed.")})
    now = now or timezone.now()
    changes = {"note": [None, text]}
    fields = ["updated_at"]
    if contacted and p.contacted_at is None:
        p.contacted_at = now
        fields.append("contacted_at")
    if p.is_open and next_contact_on != p.next_contact_on:
        changes["next_contact_on"] = [p.next_contact_on.isoformat() if p.next_contact_on else None,
                                      next_contact_on.isoformat() if next_contact_on else None]
        p.next_contact_on = next_contact_on
        fields.append("next_contact_on")
    p.save(update_fields=fields)
    note = _note(p, Kind.CONTACT if contacted else Kind.NOTE, text, actor.user, now)
    status = _settle(p)
    if status:
        changes["status"] = status
    _audit("prospects.note", actor.user, actor.organization, p, request, changes)
    return note


# ---------------------------------------------------------------------------
# Viewings
# ---------------------------------------------------------------------------


@transaction.atomic
def book_viewing(actor: Membership, p: Prospect, *, unit: Unit, scheduled_at: datetime.datetime, with_user=None,
                 now: datetime.datetime | None = None, request=None) -> Viewing:
    p = _lock(p)
    _require(actor, p)
    _require_open(p)
    now = now or timezone.now()
    errors = {}
    if unit is None or unit.property_id != p.property_id or unit.is_archived:
        errors["unit"] = _("Choose a unit of this property.")
    if scheduled_at is None:
        errors["scheduled_at"] = _("Choose when.")
    elif not now - VIEWING_PAST <= scheduled_at <= now + VIEWING_AHEAD:
        errors["scheduled_at"] = _("Choose a time between a week ago and 90 days from now.")
    if with_user is not None and with_user.pk not in {u.pk for u in assignable_users(p.property)}:
        errors["with_user"] = _("That person cannot work on prospects at this property.")
    if errors:
        raise ValidationError(errors)
    viewing = Viewing.objects.create(prospect=p, unit=unit, scheduled_at=scheduled_at, with_user=with_user,
                                     created_by=actor.user)
    fields = ["updated_at"]
    if p.contacted_at is None:  # a viewing is only booked with someone who was reached
        p.contacted_at = now
        fields.append("contacted_at")
    p.save(update_fields=fields)
    text = _("Viewing of %(unit)s on %(when)s") % {"unit": unit.code, "when": _when(scheduled_at)}
    if with_user is not None:
        text += " " + _("with %(who)s") % {"who": with_user}
    _note(p, Kind.VIEWING, text, actor.user, now)
    changes = {"unit": [None, unit.code], "scheduled_at": [None, scheduled_at.isoformat()],
               **({"with": [None, str(with_user)]} if with_user else {})}
    status = _settle(p)
    if status:
        changes["status"] = status
    _audit("prospects.viewing", actor.user, actor.organization, p, request, changes)
    if with_user is not None and with_user.pk != actor.user_id:
        _notify_viewing(viewing, with_user, actor.user)
    return viewing


@transaction.atomic
def viewing_outcome(actor: Membership, viewing: Viewing, status: str, *, outcome: str = "",
                    request=None) -> Viewing:
    """Done, did not come, or cancelled. Only a booked viewing can be settled."""
    p = _lock(viewing.prospect)
    _require(actor, p)
    viewing = Viewing.objects.select_for_update(of=("self",)).select_related("unit").get(pk=viewing.pk, prospect=p)
    if viewing.status != Viewing.Status.BOOKED:
        raise ValidationError(_("This viewing is already settled."))
    if status not in (Viewing.Status.DONE, Viewing.Status.NO_SHOW, Viewing.Status.CANCELLED):
        raise ValidationError(_("Unknown action."))
    outcome = (outcome or "").strip()[:300]
    viewing.status, viewing.outcome = status, outcome
    viewing.save(update_fields=["status", "outcome"])
    label = Viewing.Status(status).label
    _note(p, Kind.VIEWING, f"{viewing.unit.code}: {label}" + (f". {outcome}" if outcome else ""), actor.user)
    changes = {"viewing": [Viewing.Status.BOOKED, status], "unit": [None, viewing.unit.code],
               **({"outcome": [None, outcome]} if outcome else {})}
    new_status = _settle(p)
    if new_status:
        changes["status"] = new_status
    _audit("prospects.viewing_outcome", actor.user, actor.organization, p, request, changes)
    return viewing


# ---------------------------------------------------------------------------
# Holding a unit (D-072 item 4)
# ---------------------------------------------------------------------------


def _set_unit_status(unit: Unit, status: str, user, org, request, prospect: Prospect) -> None:
    old = unit.manual_status
    if old == status:
        return
    unit.manual_status = status
    unit.save(update_fields=["manual_status", "updated_at"])
    audit.record("unit.status", actor=user, organization=org, obj=unit, request=request,
                 changes={"manual_status": [old, status], "prospect": [None, prospect.name]})


def _release(p: Prospect, user, org, text: str, request, *, touch_unit: bool = True) -> None:
    """Clears the hold. The unit goes back to normal only if it is still Reserved."""
    unit = Unit.all_objects.select_for_update().get(pk=p.held_unit_id)
    held_until = p.held_until
    p.held_unit, p.held_until = None, None
    p.save(update_fields=["held_unit", "held_until", "updated_at"])
    if touch_unit and unit.manual_status == Unit.ManualStatus.RESERVED:
        _set_unit_status(unit, Unit.ManualStatus.NORMAL, user, org, request, p)
    _note(p, Kind.RELEASE, text or _("%(unit)s released") % {"unit": unit.code}, user)
    changes = {"held_unit": [unit.code, None], "held_until": [held_until.isoformat(), None]}
    status = _settle(p)
    if status:
        changes["status"] = status
    _audit("prospects.release", user, org, p, request, {**changes, **({"note": [None, text]} if text else {})})


@transaction.atomic
def hold_unit(actor: Membership, p: Prospect, *, unit: Unit, until: datetime.date,
              today: datetime.date | None = None, request=None) -> Prospect:
    """Reserves one unit for this prospect until a date: its manual status becomes Reserved."""
    p = _lock(p)
    _require(actor, p)
    if p.status not in Prospect.OPEN:
        raise ValidationError(_("This prospect is closed. Reopen it first."))
    today = today or timezone.localdate()
    errors = {}
    if unit is None or unit.property_id != p.property_id or unit.is_archived:
        errors["unit"] = _("Choose a unit of this property.")
    if until is None or not today <= until <= today + datetime.timedelta(days=Prospect.MAX_HOLD_DAYS):
        errors["until"] = _("Hold it for at most %(n)s days from today.") % {"n": Prospect.MAX_HOLD_DAYS}
    if errors:
        raise ValidationError(errors)
    unit = Unit.all_objects.select_for_update().get(pk=unit.pk)
    same = p.held_unit_id == unit.pk
    if not same:
        other = Prospect.objects.filter(held_unit=unit).exclude(pk=p.pk).first()
        if other is not None:
            raise ValidationError({"unit": _("%(unit)s is already held for %(name)s.")
                                   % {"unit": unit.code, "name": other.name}})
        if not is_available_to_let(unit):
            raise ValidationError({"unit": _("%(unit)s is not available to let.") % {"unit": unit.code}})
        if p.held_unit_id:
            _release(p, actor.user, actor.organization, _("Moved the hold to %(unit)s") % {"unit": unit.code},
                     request)
    before = p.held_until
    p.held_unit, p.held_until = unit, until
    p.save(update_fields=["held_unit", "held_until", "updated_at"])
    _set_unit_status(unit, Unit.ManualStatus.RESERVED, actor.user, actor.organization, request, p)
    _note(p, Kind.HOLD, _("%(unit)s held until %(until)s") % {"unit": unit.code, "until": f"{until:%d/%m/%Y}"},
          actor.user)
    changes = {"held_unit": [None if not same else unit.code, unit.code],
               "held_until": [before.isoformat() if same and before else None, until.isoformat()]}
    status = _settle(p)
    if status:
        changes["status"] = status
    _audit("prospects.hold", actor.user, actor.organization, p, request, changes)
    return p


@transaction.atomic
def release_unit(actor: Membership, p: Prospect, *, note: str = "", request=None) -> Prospect:
    p = _lock(p)
    _require(actor, p)
    if not p.held_unit_id:
        raise ValidationError(_("No unit is held for this prospect."))
    _release(p, actor.user, actor.organization, (note or "").strip()[:300], request)
    return p


def lease_activated(actor: Membership, lease, request=None) -> None:
    """A lease on a held unit ends the hold (called from `leases.services.activate_lease`)."""
    for p in Prospect.objects.filter(held_unit_id=lease.unit_id).select_for_update(of=("self",)):
        _release(p, actor.user, actor.organization,
                 _("%(unit)s let on lease %(lease)s") % {"unit": lease.unit.code, "lease": lease.number}, request)


def unit_status_changed(actor: Membership, unit: Unit, request=None) -> None:
    """Someone took the unit off Reserved by hand: the hold ends, the new status stays."""
    for p in Prospect.objects.filter(held_unit_id=unit.pk).select_for_update(of=("self",)):
        _release(p, actor.user, actor.organization,
                 _("%(unit)s set to %(status)s on the unit page") % {
                     "unit": unit.code, "status": unit.get_manual_status_display().lower()},
                 request, touch_unit=False)


# ---------------------------------------------------------------------------
# Closing
# ---------------------------------------------------------------------------


@transaction.atomic
def convert(actor: Membership, p: Prospect, *, tenant: Tenant | None = None,
            now: datetime.datetime | None = None, request=None) -> Tenant:
    """Makes the prospect a tenant: a new tenant record, or one that already exists (D-072 item 5).

    The hold stays until the lease is activated, so the unit is not let to someone else meanwhile.
    """
    p = _lock(p)
    _require(actor, p)
    _require_open(p)
    require(actor, "tenants.manage")
    if tenant is None:
        tenant = tenant_services.create_tenant(actor, name=p.name, phone=p.phone, email=p.email, request=request)
    elif (tenant.organization_id != actor.organization_id or tenant.is_archived
          or not tenant_services.visible_tenants(actor).filter(pk=tenant.pk).exists()):
        raise PermissionDenied("tenants.view")
    now = now or timezone.now()
    before = p.status
    p.tenant, p.status, p.converted_at, p.next_contact_on = tenant, Status.CONVERTED, now, None
    p.save(update_fields=["tenant", "status", "converted_at", "next_contact_on", "updated_at"])
    _note(p, Kind.CONVERTED, _("Tenant %(name)s") % {"name": tenant.name}, actor.user, now)
    _audit("prospects.convert", actor.user, actor.organization, p, request,
           {"status": [before, Status.CONVERTED], "tenant": [None, tenant.name]})
    return tenant


@transaction.atomic
def lose(actor: Membership, p: Prospect, *, reason: str, note: str = "", now: datetime.datetime | None = None,
         request=None) -> Prospect:
    """Closes the prospect: the hold is released and booked viewings are cancelled."""
    p = _lock(p)
    _require(actor, p)
    _require_open(p)
    note = (note or "").strip()[:300]
    if reason not in Prospect.LostReason.values:
        raise ValidationError({"reason": _("Choose why.")})
    if reason == Prospect.LostReason.OTHER and not note:
        raise ValidationError({"note": _("Say why.")})
    now = now or timezone.now()
    if p.held_unit_id:
        _release(p, actor.user, actor.organization, _("Prospect lost"), request)
    cancelled = p.viewings.filter(status=Viewing.Status.BOOKED).update(status=Viewing.Status.CANCELLED,
                                                                         outcome=_("Prospect lost"))
    before = p.status
    p.status, p.lost_at, p.lost_reason, p.lost_note, p.next_contact_on = Status.LOST, now, reason, note, None
    p.save(update_fields=["status", "lost_at", "lost_reason", "lost_note", "next_contact_on", "updated_at"])
    label = Prospect.LostReason(reason).label
    _note(p, Kind.LOST, f"{label}" + (f". {note}" if note else ""), actor.user, now)
    _audit("prospects.lose", actor.user, actor.organization, p, request,
           {"status": [before, Status.LOST], "reason": [None, reason], **({"note": [None, note]} if note else {}),
            **({"viewings_cancelled": [None, cancelled]} if cancelled else {})})
    return p


@transaction.atomic
def reopen(actor: Membership, p: Prospect, *, request=None) -> Prospect:
    p = _lock(p)
    _require(actor, p)
    if p.status != Status.LOST:
        raise ValidationError(_("Only a lost prospect can be reopened."))
    p.status = _derive(p)
    p.lost_at, p.lost_reason, p.lost_note = None, "", ""
    p.save(update_fields=["status", "lost_at", "lost_reason", "lost_note", "updated_at"])
    _note(p, Kind.REOPENED, "", actor.user)
    _audit("prospects.reopen", actor.user, actor.organization, p, request, {"status": [Status.LOST, p.status]})
    return p


# ---------------------------------------------------------------------------
# Enquiries from a vacancy link (D-072 item 6)
# ---------------------------------------------------------------------------


def _enquiry_owner(unit: Unit):
    """Whoever shared the link, while they still work on prospects there."""
    user = unit.shared_by
    if user is None or not user.is_active:
        return None
    m = (Membership.objects.filter(organization_id=unit.organization_id, user=user, is_active=True,
                                   archived_at__isnull=True).select_related("organization", "role", "user").first())
    return user if m is not None and can(m, "prospects.manage", unit.property) else None


@transaction.atomic
def public_enquiry(unit: Unit, *, name: str, phone: str, message: str = "", now: datetime.datetime | None = None,
                   request=None) -> Prospect:
    """Someone asked about a unit from its public link. The same phone asking again adds to the open prospect."""
    errors = {}
    name = " ".join((name or "").split())[:150]
    if not name:
        errors["name"] = _("Enter your name.")
    try:
        phone = normalize_phone(phone)
    except InvalidPhoneNumber as exc:
        errors["phone"] = str(exc)
    if errors:
        raise ValidationError(errors)
    message = (message or "").strip()[:500]
    now = now or timezone.now()
    org, prop = unit.organization, unit.property
    text = _("Asked about %(unit)s from the vacancy link.") % {"unit": unit.code}
    if message:
        text += " " + message
    p = (Prospect.objects.select_for_update(of=("self",))
         .filter(organization=org, property=prop, phone=phone, status__in=Prospect.OPEN).order_by("-pk").first())
    if p is not None:
        if p.unit_id is None:
            p.unit = unit
            p.save(update_fields=["unit", "updated_at"])
        note = _note(p, Kind.ENQUIRY, text, None, now)
        _audit("prospects.enquiry", None, org, p, request, {"unit": [None, unit.code], "again": [None, True]})
    else:
        p = Prospect.objects.create(organization=org, property=prop, unit=unit, name=name, phone=phone,
                                    source=Prospect.Source.VACANCY_LINK, assigned_to=_enquiry_owner(unit))
        note = _note(p, Kind.ENQUIRY, text, None, now)
        _audit("prospects.enquiry", None, org, p, request,
               {"property": [None, prop.name], "unit": [None, unit.code], "name": [None, name],
                "source": [None, p.source]})
    recipients = [p.assigned_to] if p.assigned_to_id else staff_with(org, "prospects.manage", prop)
    for user in recipients:
        notify(org, "prospect_new", user=user, dedupe_key=f"prospect_new:{note.pk}:{user.pk}",
               context={"name": p.name, "phone": p.phone, "property": prop.name, "unit": unit.code,
                        "source": p.get_source_display()})
    return p


# What a hostel enquiry asked for, kept in `Prospect.wants` in these words whatever the language.
HOSTEL_WANTS = {"bed": "Bed", "room": "Whole room", "any": "Bed or room"}


@transaction.atomic
def hostel_enquiry(prop: Property, *, name: str, phone: str, gender: str = "", wants: str = "", institution: str = "",
                   move_in_by=None, message: str = "", now: datetime.datetime | None = None, request=None) -> Prospect:
    """A student asked for a bed or room on a hostel's booking page (D-189).

    With nothing suitable free the student goes on the waitlist instead; staff see both in the same list.
    """
    from properties import hostel

    errors = {}
    name = " ".join((name or "").split())[:150]
    if not name:
        errors["name"] = _("Enter your name.")
    try:
        phone = normalize_phone(phone)
    except InvalidPhoneNumber as exc:
        errors["phone"] = str(exc)
    if gender not in ("F", "M"):
        errors["gender"] = _("Choose female or male, so we can offer the right room.")
    if wants not in ("bed", "room", "any"):
        errors["wants"] = _("Choose a bed or a whole room.")
    if errors:
        raise ValidationError(errors)
    org = prop.organization
    now = now or timezone.now()
    whole = {"bed": False, "room": True}.get(wants)
    waitlisted = not hostel.free_beds(prop, gender, whole).exists()
    what = HOSTEL_WANTS[wants]
    parts = [_("Asked for: %(what)s, from the booking page.") % {"what": what}]
    if institution:
        parts.append(_("College or university: %(i)s.") % {"i": institution[:120]})
    if waitlisted:
        parts.append(_("Nothing suitable was free, so they are on the waitlist."))
    if message.strip():
        parts.append(message.strip()[:500])
    text = " ".join(parts)
    owner = prop.booking_shared_by if prop.booking_shared_by and prop.booking_shared_by.is_active else None
    p = (Prospect.objects.select_for_update(of=("self",))
         .filter(organization=org, property=prop, phone=phone, status__in=Prospect.OPEN).order_by("-pk").first())
    if p is not None:
        p.gender, p.wants = gender, what[:300]
        p.waitlisted = p.waitlisted or waitlisted
        p.save(update_fields=["gender", "wants", "waitlisted", "updated_at"])
        note = _note(p, Kind.ENQUIRY, text, None, now)
        _audit("prospects.enquiry", None, org, p, request, {"again": [None, True], "waitlisted": [None, waitlisted]})
    else:
        p = Prospect.objects.create(organization=org, property=prop, name=name, phone=phone, gender=gender,
                                    wants=what[:300], move_in_by=move_in_by, waitlisted=waitlisted,
                                    source=Prospect.Source.VACANCY_LINK, assigned_to=owner)
        note = _note(p, Kind.ENQUIRY, text, None, now)
        _audit("prospects.enquiry", None, org, p, request,
               {"property": [None, prop.name], "name": [None, name], "source": [None, p.source],
                "waitlisted": [None, waitlisted]})
    recipients = [p.assigned_to] if p.assigned_to_id else staff_with(org, "prospects.manage", prop)
    label = _("the waitlist") if waitlisted else what
    for user in recipients:
        notify(org, "prospect_new", user=user, dedupe_key=f"prospect_new:{note.pk}:{user.pk}",
               context={"name": p.name, "phone": p.phone, "property": prop.name, "unit": label,
                        "source": p.get_source_display()})
    return p


def waitlist(prop: Property):
    """Open prospects waiting for a bed (none yet held a unit), longest waiting first."""
    return (Prospect.objects.filter(property=prop, waitlisted=True, status__in=Prospect.OPEN, held_unit__isnull=True)
            .order_by("created_at", "pk"))


OFFER_DAYS = 3


def offer_free_beds(today: datetime.date | None = None, now: datetime.datetime | None = None) -> dict[str, int]:
    """D-190: text the waitlisted students whose kind of bed is now free, first come first served.

    A bed is offered once: a student already offered within OFFER_DAYS counts against the free beds of their gender,
    so two students are not sent after one bed. An offer that lapsed is not repeated; the prospect stays on the
    waitlist for staff to call.
    """
    from properties import hostel

    now = now or timezone.now()
    today = today or timezone.localdate()
    sent = 0
    props = (Property.objects.filter(prospects__waitlisted=True, prospects__status__in=Prospect.OPEN,
                                     prospects__held_unit__isnull=True, prospects__offered_at__isnull=True,
                                     organization__org_type="HOSTEL").distinct().select_related("organization"))
    for prop in props:
        waiting = list(waitlist(prop))
        cutoff = now - datetime.timedelta(days=OFFER_DAYS)
        pending = [p for p in waiting if p.offered_at and p.offered_at >= cutoff]
        for p in waiting:
            if p.offered_at or not p.gender:
                continue
            whole = {"Bed": False, "Whole room": True}.get(p.wants)
            free = hostel.free_beds(prop, p.gender, whole, today).count()
            if free <= sum(1 for q in pending if q.gender == p.gender):
                continue
            with transaction.atomic():
                note_text = _("Texted that a bed is free; they have %(n)s days to take it.") % {"n": OFFER_DAYS}
                contact = prop.booking_shared_by or p.assigned_to
                if contact is None:
                    users = staff_with(prop.organization, "prospects.manage", prop)
                    contact = users[0] if users else None
                who = (contact.phone if contact is not None else "") or prop.organization.display_name
                message = notify(prop.organization, "hostel_bed_free", phone=p.phone,
                                 dedupe_key=f"hostel_bed_free:{p.pk}",
                                 context={"name": p.name, "property": prop.name, "contact": who, "days": OFFER_DAYS})
                p.offered_at = now
                p.save(update_fields=["offered_at", "updated_at"])
                _note(p, Kind.NOTE, note_text, None, now)
                _audit("prospects.bed_offer", None, prop.organization, p, None, {"offered_at": [None, str(now)]})
            pending.append(p)
            sent += 1 if message is not None else 0
    return {"bed_offers": sent}


# ---------------------------------------------------------------------------
# Lists and home tasks
# ---------------------------------------------------------------------------


def _managed(membership: Membership):
    if not can(membership, "prospects.manage"):
        return Prospect.objects.none()
    ids = [p.pk for p in manageable_properties(membership)]
    return visible_prospects(membership).filter(property_id__in=ids)


def follow_ups_due(membership: Membership, today: datetime.date | None = None):
    """Open prospects to call: new ones never reached, and follow-ups due by today."""
    today = today or timezone.localdate()
    return _managed(membership).filter(status__in=Prospect.OPEN).filter(
        Q(status=Status.NEW) | Q(next_contact_on__lte=today))


def viewings_on(membership: Membership, day: datetime.date):
    return (Viewing.objects.filter(prospect__in=_managed(membership), status=Viewing.Status.BOOKED,
                                   scheduled_at__date=day))


def lapsed_holds(membership: Membership, today: datetime.date | None = None):
    return _managed(membership).filter(held_unit__isnull=False, held_until__lt=today or timezone.localdate())


def upcoming_viewings(membership: Membership, now: datetime.datetime | None = None, days: int = 7):
    now = now or timezone.now()
    start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return (Viewing.objects.filter(prospect__in=visible_prospects(membership), status=Viewing.Status.BOOKED,
                                   scheduled_at__gte=start, scheduled_at__lt=start + datetime.timedelta(days=days))
            .select_related("prospect", "unit", "with_user").order_by("scheduled_at", "pk"))


@dataclass
class Summary:
    open: int
    to_call: int
    converted: int
    lost: int

    @property
    def conversion(self) -> int | None:
        """Share of prospects closed in the last 90 days who became tenants, in percent."""
        closed = self.converted + self.lost
        return round(100 * self.converted / closed) if closed else None


def summary(queryset, today: datetime.date | None = None, now: datetime.datetime | None = None) -> Summary:
    today = today or timezone.localdate()
    since = (now or timezone.now()) - datetime.timedelta(days=STATS_DAYS)
    agg = queryset.aggregate(
        open=Count("pk", filter=Q(status__in=Prospect.OPEN)),
        to_call=Count("pk", filter=Q(status__in=Prospect.OPEN) & (Q(status=Status.NEW)
                                                                  | Q(next_contact_on__lte=today))),
        converted=Count("pk", filter=Q(status=Status.CONVERTED, converted_at__gte=since)),
        lost=Count("pk", filter=Q(status=Status.LOST, lost_at__gte=since)))
    return Summary(**agg)


def pipeline(queryset, now: datetime.datetime | None = None) -> dict:
    """D-140: open prospects by stage, and where the last STATS_DAYS days' enquiries came from and why some were lost.

    Rows are (label, count), in stage order for `stages` and largest first for the others; zeros are left out of
    `sources` and `lost`."""
    since = (now or timezone.now()) - datetime.timedelta(days=STATS_DAYS)
    by_status = dict(queryset.order_by().values_list("status").annotate(n=Count("pk")))
    labels = dict(Status.choices)
    stages = [(str(labels[s]), by_status.get(s, 0)) for s in Prospect.OPEN]
    sources = dict(Prospect.Source.choices)
    came = queryset.filter(created_at__gte=since).order_by().values_list("source").annotate(n=Count("pk"))
    reasons = dict(Prospect.LostReason.choices)
    lost = (queryset.filter(status=Status.LOST, lost_at__gte=since).order_by().values_list("lost_reason")
            .annotate(n=Count("pk")))
    return {
        "stages": stages,
        "sources": sorted(((str(sources.get(k, k)), n) for k, n in came if n), key=lambda r: -r[1]),
        "lost": sorted(((str(reasons.get(k) or _("No reason given")), n) for k, n in lost if n), key=lambda r: -r[1]),
        "days": STATS_DAYS,
    }


def held_for(unit: Unit) -> Prospect | None:
    return Prospect.objects.filter(held_unit=unit).select_related("assigned_to").first()


# ---------------------------------------------------------------------------
# Notifications (D-072 item 7)
# ---------------------------------------------------------------------------


def _notify_assigned(p: Prospect, user, by, note: ProspectNote) -> None:
    notify(p.organization, "prospect_assigned", user=user, created_by=by,
           dedupe_key=f"prospect_assigned:{note.pk}:{user.pk}",
           context={"name": p.name, "phone": p.phone, "property": p.property.name,
                    "unit": p.unit.code if p.unit_id else "-", "assigned_by": str(by)})


def _notify_viewing(viewing: Viewing, user, by) -> None:
    p = viewing.prospect
    notify(p.organization, "prospect_viewing", user=user, created_by=by,
           dedupe_key=f"prospect_viewing:{viewing.pk}:{user.pk}",
           context={"name": p.name, "phone": p.phone, "property": p.property.name, "unit": viewing.unit.code,
                    "when": _when(viewing.scheduled_at), "booked_by": str(by)})
