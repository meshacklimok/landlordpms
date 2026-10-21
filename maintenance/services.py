"""Maintenance requests and jobs (D-068). Views stay thin; the rules live here."""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from accounts.models import Membership, User
from accounts.permissions import can, require, visible_properties
from audit import services as audit
from core.money import ZERO
from core.numbering import next_number
from core.phone import InvalidPhoneNumber, normalize_phone
from expenses import services as expense_services
from expenses.models import Expense, Supplier
from inspections import photos as photo_files
from leases.models import Lease
from notifications.delivery import notify, staff_with
from properties.models import Property, Unit

from .models import MaintenancePhoto, MaintenanceRequest, MaintenanceTarget, MaintenanceUpdate, OfflineReport

Status = MaintenanceRequest.Status
Priority = MaintenanceRequest.Priority
Kind = MaintenanceUpdate.Kind

MAX_PHOTOS = 10
MAX_PORTAL_PHOTOS = 3
MAX_PORTAL_PER_DAY = 5
USSD_REPEAT_WINDOW = datetime.timedelta(minutes=10)
STATS_DAYS = 90
MAX_TARGET_HOURS = 365 * 24
# Where each action may start, the status it leads to, and who may do it (D-068 item 3).
ACTIONS = {
    "start": ("maintenance.update", (Status.NEW, Status.ASSIGNED, Status.ON_HOLD), Status.IN_PROGRESS),
    "hold": ("maintenance.update", (Status.ASSIGNED, Status.IN_PROGRESS), Status.ON_HOLD),
    "done": ("maintenance.update", MaintenanceRequest.OPEN, Status.DONE),
    "close": ("maintenance.close", MaintenanceRequest.UNFINISHED, Status.CLOSED),
    "reopen": ("maintenance.close", (Status.DONE, Status.CLOSED), None),
    "cancel": ("maintenance.close", MaintenanceRequest.OPEN, Status.CANCELLED),
}
NEEDS_REASON = {"hold": gettext_lazy("Say what the job is waiting for."),
                "reopen": gettext_lazy("Say why it is reopened."),
                "cancel": gettext_lazy("Say why it is cancelled.")}


# ---------------------------------------------------------------------------
# Targets (D-074)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    respond: datetime.timedelta
    fix: datetime.timedelta
    custom: bool = False


def targets(org) -> dict[str, Target]:
    """Priority → respond-by and fix-by times: the organization's own, else the defaults (D-074 item 2)."""
    result = {p: Target(MaintenanceRequest.RESPOND_AFTER[p], MaintenanceRequest.DUE_AFTER[p])
              for p in Priority.values}
    for t in MaintenanceTarget.objects.filter(organization=org):
        result[t.priority] = Target(datetime.timedelta(hours=t.respond_hours),
                                    datetime.timedelta(hours=t.fix_hours), custom=True)
    return result


def _hours(delta: datetime.timedelta) -> int:
    return int(delta.total_seconds() // 3600)


@transaction.atomic
def save_targets(actor: Membership, rows: dict[str, tuple[int, int]], *, request=None) -> dict[str, Target]:
    """`rows`: priority → (respond hours, fix hours). A row equal to the default removes the custom one."""
    require(actor, "organization.manage")
    org = actor.organization
    errors = {}
    for priority, (respond, fix) in rows.items():
        if priority not in Priority.values:
            raise ValidationError(_("Unknown priority."))
        if not (1 <= respond <= MAX_TARGET_HOURS and 1 <= fix <= MAX_TARGET_HOURS):
            errors[priority] = _("Use between 1 hour and 365 days.")
        elif respond > fix:
            errors[priority] = _("Starting the work cannot be due after the fix.")
    if errors:
        raise ValidationError(errors)
    before = targets(org)
    changes = {}
    for priority, (respond, fix) in rows.items():
        old = before[priority]
        if (_hours(old.respond), _hours(old.fix)) == (respond, fix):
            continue
        changes[priority] = [f"{_hours(old.respond)}h/{_hours(old.fix)}h", f"{respond}h/{fix}h"]
        default = (_hours(MaintenanceRequest.RESPOND_AFTER[priority]), _hours(MaintenanceRequest.DUE_AFTER[priority]))
        if (respond, fix) == default:
            MaintenanceTarget.objects.filter(organization=org, priority=priority).delete()
        else:
            MaintenanceTarget.objects.update_or_create(
                organization=org, priority=priority,
                defaults={"respond_hours": respond, "fix_hours": fix, "updated_by": actor.user})
    if changes:
        audit.record("maintenance.targets", actor=actor.user, organization=org, obj=org, request=request,
                     changes=changes)
    return targets(org)


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def visible_requests(membership: Membership, queryset=None):
    """Every request on the member's properties with `maintenance.view`; with `view_assigned`, their own jobs."""
    qs = (queryset if queryset is not None else MaintenanceRequest.objects.all()).for_org(membership.organization)
    props = visible_properties(membership, Property.all_objects.all()).values("pk")
    q = Q(pk__in=[])
    if can(membership, "maintenance.view"):
        q |= Q(property__in=props)
    if can(membership, "maintenance.view_assigned"):
        q |= Q(property__in=props, assigned_to=membership.user)
    return qs.filter(q)


def sees(membership: Membership, req: MaintenanceRequest) -> bool:
    if req.organization_id != membership.organization_id:
        return False
    if can(membership, "maintenance.view", req.property):
        return True
    return req.assigned_to_id == membership.user_id and can(membership, "maintenance.view_assigned", req.property)


def reportable_properties(membership: Membership) -> list[Property]:
    """Live properties where the member may report a request."""
    return [p for p in visible_properties(membership, Property.objects.all()).order_by("name")
            if can(membership, "maintenance.create", p)]


def assignable_users(req_or_property) -> list:
    """Staff who could see the job if it were theirs (D-068 item 4)."""
    prop = req_or_property.property if isinstance(req_or_property, MaintenanceRequest) else req_or_property
    memberships = (Membership.objects.filter(organization_id=prop.organization_id, is_active=True,
                                             archived_at__isnull=True, user__is_active=True)
                   .select_related("user", "organization", "role").order_by("user__full_name", "user__pk"))
    return [m.user for m in memberships
            if can(m, "maintenance.view", prop) or can(m, "maintenance.view_assigned", prop)]


def can_see_costs(membership: Membership, req: MaintenanceRequest) -> bool:
    return can(membership, "maintenance.costs", req.property) or can(membership, "expenses.view", req.property)


def can_record_cost(membership: Membership, req: MaintenanceRequest) -> bool:
    return (req.status != Status.CANCELLED and not req.property.is_archived
            and can(membership, "maintenance.costs", req.property)
            and can(membership, "expenses.submit", req.property))


def allowed_actions(membership: Membership, req: MaintenanceRequest) -> list[str]:
    if not sees(membership, req):
        return []
    return [name for name, (cap, froms, _to) in ACTIONS.items()
            if req.status in froms and can(membership, cap, req.property)]


def can_update(membership: Membership, req: MaintenanceRequest) -> bool:
    return sees(membership, req) and can(membership, "maintenance.update", req.property)


def can_assign(membership: Membership, req: MaintenanceRequest) -> bool:
    return (req.status in MaintenanceRequest.OPEN and sees(membership, req)
            and can(membership, "maintenance.assign", req.property))


def active_lease(unit: Unit | None) -> Lease | None:
    if unit is None:
        return None
    return (Lease.objects.filter(unit=unit, status=Lease.Status.ACTIVE)
            .prefetch_related("lease_tenants__tenant").order_by("-start_date", "-pk").first())


def _require(actor: Membership, req: MaintenanceRequest, capability: str) -> None:
    if not sees(actor, req):
        raise PermissionDenied(capability)
    require(actor, capability, req.property)


def _audit(action, user, org, obj, request, changes=None):
    audit.record(action, actor=user, organization=org, obj=obj, request=request, changes=changes or {})


def _lock(req: MaintenanceRequest) -> MaintenanceRequest:
    return (MaintenanceRequest.objects.select_for_update(of=("self",))
            .select_related("property", "unit", "tenant", "lease", "organization", "assigned_to", "supplier")
            .get(pk=req.pk))


def _text(value: str, limit: int) -> str:
    return (value or "").strip()[:limit]


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------


def _add_photos(req: MaintenanceRequest, files, user, *, limit: int = MAX_PHOTOS, shared: bool = False) -> int:
    files = [f for f in (files or []) if f]
    if not files:
        return 0
    if req.photos.count() + len(files) > limit:
        raise ValidationError({"photos": _("A request can have at most %(n)s photos.") % {"n": limit}})
    processed = []
    for upload in files:
        try:
            processed.append(photo_files.process(upload))
        except ValidationError as exc:
            raise ValidationError({"photos": exc.messages}) from None
    for image, width, height in processed:
        MaintenancePhoto.objects.create(request=req, image=image, width=width, height=height, uploaded_by=user,
                                        shared_with_tenant=shared)
    return len(processed)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _clean_report(title, description, kind, priority) -> tuple[dict, dict]:
    errors = {}
    title = " ".join((title or "").split())[:120]
    if not title:
        errors["title"] = _("Say what is wrong.")
    if kind not in MaintenanceRequest.Kind.values:
        errors["kind"] = _("Choose the kind of problem.")
    if priority not in Priority.values:
        errors["priority"] = _("Choose how urgent it is.")
    return {"title": title, "description": _text(description, 2000), "kind": kind, "priority": priority}, errors


@transaction.atomic
def report(actor: Membership, prop: Property, *, title: str, description: str = "", kind: str = "OTHER",
           priority: str = Priority.NORMAL, unit: Unit | None = None, from_tenant: bool = False, photos=None,
           now: datetime.datetime | None = None, request=None) -> MaintenanceRequest:
    """A request reported by staff. `from_tenant` makes it the request of the unit's tenant (D-068 item 7)."""
    if prop.organization_id != actor.organization_id:
        raise PermissionDenied("maintenance.create")
    require(actor, "maintenance.create", prop)
    fields, errors = _clean_report(title, description, kind, priority)
    if prop.is_archived:
        errors["property"] = _("This property is archived.")
    if unit is not None and (unit.property_id != prop.pk or unit.is_archived):
        errors["unit"] = _("Choose a unit of this property.")
    lease = tenant = None
    if from_tenant:
        lease = active_lease(unit) if unit is not None else None
        tenant = lease.primary_tenant if lease else None
        if tenant is None:
            errors["from_tenant"] = _("The unit has no tenant on an active lease.")
    if errors:
        raise ValidationError(errors)
    return _create(actor.organization, prop, unit, actor.user, fields, lease=lease, tenant=tenant, photos=photos,
                   source=MaintenanceRequest.Source.STAFF, now=now, request=request)


OFFLINE_BACKDATE = datetime.timedelta(days=7)


def report_offline(actor: Membership, prop: Property, *, key: str, typed_at: datetime.datetime | None = None,
                   now: datetime.datetime | None = None, request=None,
                   **fields) -> tuple[MaintenanceRequest, bool]:
    """A report kept on a phone and sent later (D-090). Returns (the request, whether it was made now);
    a report sent again with the same id returns the request made the first time (item 3).

    The request dates from when it was typed, unless that is in the future or over 7 days back (item 4).
    """
    now = now or timezone.now()
    done = OfflineReport.objects.filter(user=actor.user, key=key).select_related("request").first()
    if done is not None:
        return done.request, False
    when = typed_at if typed_at is not None and now - OFFLINE_BACKDATE <= typed_at <= now else now
    try:
        with transaction.atomic():
            req = report(actor, prop, now=when, request=request, **fields)
            OfflineReport.objects.create(organization=actor.organization, user=actor.user, key=key, request=req,
                                         typed_at=typed_at)
            _audit("maintenance.offline_report", actor.user, actor.organization, req, request, {
                "report": [None, key], "typed_at": [None, typed_at.isoformat() if typed_at else None],
                "received_at": [None, now.isoformat()]})
    except IntegrityError:
        done = OfflineReport.objects.filter(user=actor.user, key=key).select_related("request").first()
        if done is None:
            raise
        return done.request, False
    return req, True


@transaction.atomic
def portal_report(user, lease: Lease, *, title: str, description: str = "", emergency: bool = False, photos=None,
                  now: datetime.datetime | None = None, request=None) -> MaintenanceRequest:
    """A request from the tenant portal, on a unit of one of the user's active leases."""
    from portal import selectors as portal

    now = now or timezone.now()
    if lease.pk not in portal.own_lease_ids(user) or lease.status != Lease.Status.ACTIVE:
        raise PermissionDenied("portal")
    account = portal.live_accounts(user).filter(tenant__lease_links__lease=lease).select_related("tenant").first()
    if account is None:
        raise PermissionDenied("portal")
    day_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    if MaintenanceRequest.objects.filter(reported_by=user, source=MaintenanceRequest.Source.PORTAL,
                                         created_at__gte=day_start).count() >= MAX_PORTAL_PER_DAY:
        raise ValidationError(_("You have reported %(n)s problems today. Call the office if there are more.")
                              % {"n": MAX_PORTAL_PER_DAY})
    fields, errors = _clean_report(title, description, MaintenanceRequest.Kind.OTHER,
                                   Priority.EMERGENCY if emergency else Priority.NORMAL)
    if len([f for f in (photos or []) if f]) > MAX_PORTAL_PHOTOS:
        errors["photos"] = _("Add at most %(n)s photos.") % {"n": MAX_PORTAL_PHOTOS}
    if errors:
        raise ValidationError(errors)
    unit = lease.unit
    return _create(lease.organization, unit.property, unit, user, fields, lease=lease, tenant=account.tenant,
                   photos=photos, source=MaintenanceRequest.Source.PORTAL, now=now, request=request)


@transaction.atomic
def ussd_report(lease: Lease, tenant, *, kind: str, description: str = "", emergency: bool = False,
                now: datetime.datetime | None = None, source: str = MaintenanceRequest.Source.USSD
                ) -> MaintenanceRequest:
    """A request from the USSD menu (D-085): the caller's main phone vouches for `tenant`, so no user is behind it.

    The same report again within 10 minutes gives back the first one (a gateway may send the last step twice).
    The tenant assistant reports the same way with its own source (D-094 item 6).
    """
    now = now or timezone.now()
    lease = (Lease.objects.select_for_update(of=("self",)).select_related("organization", "unit__property")
             .get(pk=lease.pk))
    if lease.status != Lease.Status.ACTIVE or not lease.lease_tenants.filter(tenant=tenant).exists():
        raise PermissionDenied("ussd")
    description = " ".join((description or "").split())[:100]
    if kind not in MaintenanceRequest.Kind.values:
        raise ValidationError({"kind": _("Choose the kind of problem.")})
    title = description or str(MaintenanceRequest.Kind(kind).label)
    priority = Priority.EMERGENCY if emergency else Priority.NORMAL
    mine = MaintenanceRequest.objects.filter(lease=lease, source=source)
    repeat = mine.filter(kind=kind, title=title, created_at__gte=now - USSD_REPEAT_WINDOW).first()
    if repeat is not None:
        return repeat
    day_start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    if mine.filter(created_at__gte=day_start).count() >= MAX_PORTAL_PER_DAY:
        raise ValidationError(_("You have reported %(n)s problems today. Call the office if there are more.")
                              % {"n": MAX_PORTAL_PER_DAY})
    fields, errors = _clean_report(title, description, kind, priority)
    if errors:
        raise ValidationError(errors)
    unit = lease.unit
    return _create(lease.organization, unit.property, unit, None, fields, lease=lease, tenant=tenant, photos=None,
                   source=source, now=now, request=None)


def _create(org, prop, unit, user, fields, *, lease, tenant, photos, source, now, request) -> MaintenanceRequest:
    now = now or timezone.now()
    target = targets(org)[fields["priority"]]
    req = MaintenanceRequest.objects.create(
        organization=org, property=prop, unit=unit, lease=lease, tenant=tenant, reported_by=user, source=source,
        respond_by=now + target.respond, due_at=now + target.fix,
        number=next_number(org, "maintenance", prefix="MNT", period=str(timezone.localdate(now).year)), **fields)
    req.created_at = now
    MaintenanceRequest.objects.filter(pk=req.pk).update(created_at=now)
    MaintenanceUpdate.objects.create(request=req, kind=Kind.REPORTED, to_status=Status.NEW, by=user, at=now,
                                     shared_with_tenant=True, text=fields["description"][:1000])
    n = _add_photos(req, photos, user, limit=MAX_PORTAL_PHOTOS if source == MaintenanceRequest.Source.PORTAL
                    else MAX_PHOTOS, shared=source == MaintenanceRequest.Source.PORTAL)
    _audit("maintenance.report", user, org, req, request, {
        "property": [None, prop.name], "unit": [None, unit.code if unit else ""], "title": [None, req.title],
        "priority": [None, req.priority], "source": [None, source],
        **({"tenant": [None, tenant.name]} if tenant else {}), **({"photos": [None, n]} if n else {})})
    _notify_reported(req, user)
    if tenant is not None:
        _tell_tenant(req, "received", _("We will let you know when it is fixed."), user)
    return req


# ---------------------------------------------------------------------------
# Triage and assignment
# ---------------------------------------------------------------------------


def can_edit_details(membership: Membership, req: MaintenanceRequest) -> bool:
    return req.status in MaintenanceRequest.OPEN and can_update(membership, req)


@transaction.atomic
def edit_details(actor: Membership, req: MaintenanceRequest, *, title: str, description: str = "", kind: str,
                 now: datetime.datetime | None = None, request=None) -> MaintenanceRequest:
    """Corrects what is wrong, the details and the kind of an open request (D-123). The history says what changed."""
    req = _lock(req)
    _require(actor, req, "maintenance.update")
    if req.status not in MaintenanceRequest.OPEN:
        raise ValidationError(_("Only an open request can be changed."))
    fields, errors = _clean_report(title, description, kind, req.priority)
    if errors:
        raise ValidationError(errors)
    del fields["priority"]
    changes = {f: [getattr(req, f), v] for f, v in fields.items() if getattr(req, f) != v}
    if not changes:
        return req
    for field, (_before, after) in changes.items():
        setattr(req, field, after)
    req.save(update_fields=[*changes, "updated_at"])
    labels = {"title": _("what is wrong"), "description": _("details"), "kind": _("kind")}
    MaintenanceUpdate.objects.create(
        request=req, kind=Kind.NOTE, by=actor.user, at=now or timezone.now(),
        text=_("Changed %(what)s.") % {"what": ", ".join(str(labels[f]) for f in changes)})
    _audit("maintenance.edit", actor.user, actor.organization, req, request, changes)
    return req


@transaction.atomic
def set_priority(actor: Membership, req: MaintenanceRequest, *, priority: str, due_at=None,
                 now: datetime.datetime | None = None, request=None) -> MaintenanceRequest:
    """A new priority resets both times from now, unless a due time is given (D-068 item 2, D-074 item 3).

    Respond by is left alone once the work has started. A due time given by hand counts as moved.
    """
    req = _lock(req)
    _require(actor, req, "maintenance.assign")
    if req.status not in MaintenanceRequest.OPEN:
        raise ValidationError(_("Only an open request can be changed."))
    if priority not in Priority.values:
        raise ValidationError({"priority": _("Choose how urgent it is.")})
    now = now or timezone.now()
    target = targets(req.organization)[priority]
    moved = due_at is not None and due_at != req.due_at
    if due_at is None:
        due_at = now + target.fix if priority != req.priority else req.due_at
    respond_by = req.respond_by
    if priority != req.priority and req.responded_at is None:
        respond_by = now + target.respond
    changes = {}
    if priority != req.priority:
        changes["priority"] = [req.priority, priority]
    if due_at != req.due_at:
        changes["due_at"] = [req.due_at.isoformat(), due_at.isoformat()]
    if respond_by != req.respond_by:
        changes["respond_by"] = [req.respond_by.isoformat(), respond_by.isoformat()]
    if not changes:
        return req
    req.priority, req.due_at, req.respond_by = priority, due_at, respond_by
    fields = ["priority", "due_at", "respond_by", "updated_at"]
    if moved:
        req.due_moved += 1
        fields.append("due_moved")
    req.save(update_fields=fields)
    MaintenanceUpdate.objects.create(
        request=req, kind=Kind.PRIORITY, by=actor.user, at=now,
        text=_("%(priority)s, due by %(due)s") % {"priority": req.get_priority_display(),
                                                  "due": _when(due_at)})
    _audit("maintenance.priority", actor.user, actor.organization, req, request, changes)
    return req


@transaction.atomic
def assign(actor: Membership, req: MaintenanceRequest, *, user=None, supplier: Supplier | None = None,
           sms_supplier: bool = True, now: datetime.datetime | None = None, request=None) -> MaintenanceRequest:
    req = _lock(req)
    _require(actor, req, "maintenance.assign")
    if req.status not in MaintenanceRequest.OPEN:
        raise ValidationError(_("Only an open request can be assigned."))
    if user is None and supplier is None:
        raise ValidationError({"assigned_to": _("Choose a staff member, a supplier or both.")})
    if user is not None and user.pk not in {u.pk for u in assignable_users(req)}:
        raise ValidationError({"assigned_to": _("That person cannot see maintenance on this property.")})
    if supplier is not None and (supplier.organization_id != req.organization_id or supplier.is_archived):
        raise ValidationError({"supplier": _("Choose a supplier from the list.")})
    now = now or timezone.now()
    changes = {}
    if (user.pk if user else None) != req.assigned_to_id:
        changes["assigned_to"] = [str(req.assigned_to) if req.assigned_to else None, str(user) if user else None]
    if (supplier.pk if supplier else None) != req.supplier_id:
        changes["supplier"] = [req.supplier.name if req.supplier else None, supplier.name if supplier else None]
    if not changes:
        return req
    new_user = user is not None and user.pk != req.assigned_to_id
    new_supplier = supplier is not None and supplier.pk != req.supplier_id
    before = req.status
    req.assigned_to, req.supplier = user, supplier
    fields = ["assigned_to", "supplier", "updated_at"]
    if req.assigned_at is None:
        req.assigned_at = now
        fields.append("assigned_at")
    if req.status == Status.NEW:
        changes["status"] = [req.status, Status.ASSIGNED]
        req.status = Status.ASSIGNED
        fields.append("status")
    req.save(update_fields=fields)
    who = [str(x) for x in (user, supplier) if x is not None]
    update = MaintenanceUpdate.objects.create(
        request=req, kind=Kind.ASSIGNED, by=actor.user, at=now, text=_("Assigned to %(who)s") % {
            "who": _(" and ").join(who)},
        from_status=before if req.status != before else "", to_status=req.status if req.status != before else "",
        shared_with_tenant=req.status != before)
    _audit("maintenance.assign", actor.user, actor.organization, req, request, changes)
    if new_user and user.pk != actor.user_id:
        _notify_assigned(req, user, actor.user, update)
    if new_supplier and sms_supplier:
        _sms_supplier(req, supplier, actor.user, update)
    return req


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@transaction.atomic
def act(actor: Membership, req: MaintenanceRequest, action: str, *, note: str = "",
        now: datetime.datetime | None = None, request=None) -> MaintenanceRequest:
    """Moves the request on: start, hold, done, close, reopen or cancel (D-068 item 3)."""
    if action not in ACTIONS:
        raise ValidationError(_("Unknown action."))
    capability, froms, to = ACTIONS[action]
    req = _lock(req)
    _require(actor, req, capability)
    if req.status not in froms:
        raise ValidationError(_("This request is %(status)s; that cannot be done now.")
                              % {"status": req.get_status_display().lower()})
    note = _text(note, 1000)
    if action in NEEDS_REASON and not note:
        raise ValidationError({"note": NEEDS_REASON[action]})
    now = now or timezone.now()
    before = req.status
    if action == "reopen":
        to = Status.ASSIGNED if (req.assigned_to_id or req.supplier_id) else Status.NEW
        req.done_at = req.closed_at = req.closed_by = None
    elif action == "start" and req.started_at is None:
        req.started_at = now
    elif action == "done":
        req.done_at = now
    elif action == "close":
        req.done_at = req.done_at or now
        req.closed_at, req.closed_by = now, actor.user
    elif action == "cancel":
        req.cancelled_at, req.cancel_reason = now, note[:300]
    req.status = to
    req.save()
    MaintenanceUpdate.objects.create(request=req, kind=Kind.STATUS, from_status=before, to_status=to, text=note,
                                     by=actor.user, at=now, shared_with_tenant=True)
    _audit("maintenance.status", actor.user, actor.organization, req, request,
           {"status": [before, to], **({"note": [None, note]} if note else {})})
    if req.tenant_id:
        if to in (Status.DONE, Status.CLOSED):
            _tell_tenant(req, "done", note or _("If the problem is still there, please tell us."), actor.user)
        elif to == Status.CANCELLED:
            _tell_tenant(req, "cancelled", note, actor.user)
    return req


@transaction.atomic
def add_note(actor: Membership, req: MaintenanceRequest, *, text: str, share: bool = False, photos=None,
             now: datetime.datetime | None = None, request=None) -> MaintenanceUpdate | None:
    """A note and/or photos. Shared notes go to the tenant (D-068 item 7)."""
    req = _lock(req)
    _require(actor, req, "maintenance.update")
    if req.status == Status.CANCELLED:
        raise ValidationError(_("This request is cancelled."))
    text = _text(text, 1000)
    files = [f for f in (photos or []) if f]
    if not text and not files:
        raise ValidationError({"text": _("Write a note or add a photo.")})
    share = share and req.tenant_id is not None
    now = now or timezone.now()
    n = _add_photos(req, files, actor.user, shared=share)
    update = None
    if text:
        update = MaintenanceUpdate.objects.create(request=req, kind=Kind.NOTE, text=text, shared_with_tenant=share,
                                                  by=actor.user, at=now)
    if n:
        MaintenanceUpdate.objects.create(request=req, kind=Kind.PHOTO, by=actor.user, at=now, shared_with_tenant=share,
                                         text=_("%(n)s photo(s) added") % {"n": n})
    _audit("maintenance.note", actor.user, actor.organization, req, request,
           {**({"note": [None, text]} if text else {}), **({"photos": [None, n]} if n else {}),
            **({"shared": [None, True]} if share else {})})
    if share and text:
        _tell_tenant(req, f"note:{update.pk}", text, actor.user)
    return update


@transaction.atomic
def portal_comment(user, req: MaintenanceRequest, *, text: str, now: datetime.datetime | None = None,
                   request=None) -> MaintenanceUpdate:
    from portal import selectors as portal

    req = _lock(req)
    if req.lease_id is None or req.lease_id not in portal.own_lease_ids(user):
        raise PermissionDenied("portal")
    if req.status in (Status.CLOSED, Status.CANCELLED):
        raise ValidationError(_("This request is finished. Report a new problem if you need to."))
    text = _text(text, 1000)
    if not text:
        raise ValidationError({"text": _("Write your comment.")})
    update = MaintenanceUpdate.objects.create(request=req, kind=Kind.COMMENT, text=text, shared_with_tenant=True,
                                              by=user, at=now or timezone.now())
    _audit("maintenance.comment", user, req.organization, req, request, {"comment": [None, text]})
    return update


# ---------------------------------------------------------------------------
# Costs
# ---------------------------------------------------------------------------


@transaction.atomic
def record_cost(actor: Membership, req: MaintenanceRequest, *, request=None, **fields) -> Expense:
    """An expense for this repair (D-068 item 6), approved like any other."""
    req = _lock(req)
    if not sees(actor, req) or not can_record_cost(actor, req):
        raise PermissionDenied("maintenance.costs")
    expense = expense_services.record_expense(actor, req.property, maintenance_request=req, request=request,
                                              **fields)
    MaintenanceUpdate.objects.create(request=req, kind=Kind.COST, by=actor.user,
                                     text=_("Cost %(number)s recorded") % {"number": expense.number})
    return expense


@dataclass
class Costs:
    expenses: list
    approved: Decimal = ZERO
    waiting: Decimal = ZERO


def costs(req: MaintenanceRequest) -> Costs:
    rows = list(req.expenses.filter(status__in=Expense.LIVE).select_related("supplier").order_by("paid_on", "pk"))
    return Costs(expenses=rows,
                 approved=sum((e.amount for e in rows if e.status == Expense.Status.APPROVED), ZERO),
                 waiting=sum((e.amount for e in rows if e.status == Expense.Status.SUBMITTED), ZERO))


# ---------------------------------------------------------------------------
# Lists and home tasks
# ---------------------------------------------------------------------------


def to_assign(membership: Membership):
    if not can(membership, "maintenance.assign"):
        return MaintenanceRequest.objects.none()
    ids = [p.pk for p in visible_properties(membership, Property.all_objects.all())
           if can(membership, "maintenance.assign", p)]
    return visible_requests(membership).filter(status=Status.NEW, property_id__in=ids)


def my_jobs(membership: Membership):
    return visible_requests(membership).filter(assigned_to=membership.user, status__in=MaintenanceRequest.OPEN)


def overdue(membership: Membership, now: datetime.datetime | None = None):
    if not can(membership, "maintenance.view"):
        return MaintenanceRequest.objects.none()
    return visible_requests(membership).filter(status__in=MaintenanceRequest.OPEN, due_at__lt=now or timezone.now())


def late_to_start(queryset, now: datetime.datetime | None = None):
    """Open, not started, and past the respond-by time (D-074 item 6)."""
    return queryset.filter(status__in=MaintenanceRequest.OPEN, started_at__isnull=True,
                           respond_by__lt=now or timezone.now())


@dataclass
class Summary:
    open: int
    overdue: int
    closed_recently: int
    avg_days_to_close: float | None
    fixed_on_time: int | None = None  # per cent of jobs done in the last 90 days that were done by the fix-by time


def summary(queryset, now: datetime.datetime | None = None) -> Summary:
    now = now or timezone.now()
    agg = queryset.aggregate(
        open=Count("pk", filter=Q(status__in=MaintenanceRequest.OPEN)),
        overdue=Count("pk", filter=Q(status__in=MaintenanceRequest.OPEN, due_at__lt=now)))
    closed = queryset.filter(status=Status.CLOSED, closed_at__gte=now - datetime.timedelta(days=STATS_DAYS))
    stats = closed.aggregate(n=Count("pk"), avg=Avg(ExpressionWrapper(F("closed_at") - F("created_at"),
                                                                      output_field=DurationField())))
    avg = round(stats["avg"].total_seconds() / 86400, 1) if stats["avg"] is not None else None
    done = queryset.exclude(status=Status.CANCELLED).filter(
        done_at__gte=now - datetime.timedelta(days=STATS_DAYS)).aggregate(
        n=Count("pk"), met=Count("pk", filter=Q(done_at__lte=F("due_at"))))
    on_time = round(100 * done["met"] / done["n"]) if done["n"] else None
    return Summary(open=agg["open"], overdue=agg["overdue"], closed_recently=stats["n"], avg_days_to_close=avg,
                   fixed_on_time=on_time)


# ---------------------------------------------------------------------------
# Performance (D-074 item 5)
# ---------------------------------------------------------------------------


@dataclass
class Performance:
    key: int | None
    name: str
    public_id: object = None
    jobs: int = 0
    finished: int = 0
    respond_met: int = 0
    respond_missed: int = 0
    fix_met: int = 0
    fix_missed: int = 0
    avg_hours_to_respond: float | None = None
    avg_days_to_fix: float | None = None
    open: int = 0
    overdue: int = 0
    reopened: int = 0
    due_moved: int = 0
    cost: Decimal | None = None

    @staticmethod
    def _pct(met, missed):
        return round(100 * met / (met + missed)) if met + missed else None

    @property
    def respond_on_time(self) -> int | None:
        return self._pct(self.respond_met, self.respond_missed)

    @property
    def fix_on_time(self) -> int | None:
        return self._pct(self.fix_met, self.fix_missed)

    @property
    def cost_per_job(self) -> Decimal | None:
        """Approved cost spread over the jobs (D-110)."""
        return None if self.cost is None or not self.jobs else (self.cost / self.jobs).quantize(Decimal("0.01"))


def _measures(now: datetime.datetime) -> dict:
    """Aggregates over requests annotated with `responded` (started, else done)."""
    return {
        "jobs": Count("pk"),
        "finished": Count("pk", filter=Q(done_at__isnull=False)),
        "respond_met": Count("pk", filter=Q(responded__lte=F("respond_by"))),
        "respond_missed": Count("pk", filter=Q(responded__gt=F("respond_by"))
                                | Q(responded__isnull=True, respond_by__lt=now)),
        "fix_met": Count("pk", filter=Q(done_at__lte=F("due_at"))),
        "fix_missed": Count("pk", filter=Q(done_at__gt=F("due_at")) | Q(done_at__isnull=True, due_at__lt=now)),
        "respond_avg": Avg(ExpressionWrapper(F("responded") - F("created_at"), output_field=DurationField())),
        "fix_avg": Avg(ExpressionWrapper(F("done_at") - F("created_at"), output_field=DurationField())),
        "open": Count("pk", filter=Q(status__in=MaintenanceRequest.OPEN)),
        "overdue": Count("pk", filter=Q(status__in=MaintenanceRequest.OPEN, due_at__lt=now)),
        "due_moved": Sum("due_moved"),
    }


def _reopened(qs):
    return MaintenanceUpdate.objects.filter(
        request__in=qs.values("pk"), kind=Kind.STATUS, from_status__in=(Status.DONE, Status.CLOSED),
        to_status__in=(Status.NEW, Status.ASSIGNED))


def _approved_costs(qs):
    return Expense.objects.filter(maintenance_request__in=qs.values("pk"), status=Expense.Status.APPROVED)


def _performance(key, name, public_id, r, reopened: int, cost: Decimal | None) -> Performance:
    return Performance(
        key=key, name=name, public_id=public_id,
        jobs=r["jobs"], finished=r["finished"], respond_met=r["respond_met"],
        respond_missed=r["respond_missed"], fix_met=r["fix_met"], fix_missed=r["fix_missed"],
        avg_hours_to_respond=round(r["respond_avg"].total_seconds() / 3600, 1) if r["respond_avg"] else None,
        avg_days_to_fix=round(r["fix_avg"].total_seconds() / 86400, 1) if r["fix_avg"] else None,
        open=r["open"], overdue=r["overdue"], reopened=reopened, due_moved=r["due_moved"] or 0, cost=cost)


# Who is on a job, and what kind of job it is (D-110): the field to group by and how each row is named.
PEOPLE = ("supplier", "assigned_to")
GROUPS = ("kind", "priority", "property")


def performance(queryset, by: str, *, now: datetime.datetime | None = None,
                with_costs: bool = False) -> list[Performance]:
    """One row per supplier (`by="supplier"`) or staff member (`by="assigned_to"`), best first; or per kind of job,
    priority or property (D-110), the kinds with most jobs first and priorities from most urgent.

    Cancelled requests do not count. Credit goes to whoever is on the request now.
    """
    now = now or timezone.now()
    if by not in PEOPLE + GROUPS:
        raise ValueError(f"Cannot rank by {by!r}.")
    qs = queryset.exclude(status=Status.CANCELLED).filter(**{f"{by}__isnull": False})
    rows = (qs.annotate(responded=Coalesce("started_at", "done_at")).values(by)
            .annotate(**_measures(now)).order_by())
    reopened = dict(_reopened(qs).values(f"request__{by}").annotate(n=Count("pk"))
                    .values_list(f"request__{by}", "n"))
    costs = {}
    if with_costs:
        costs = dict(_approved_costs(qs).values(f"maintenance_request__{by}").annotate(total=Sum("amount"))
                     .values_list(f"maintenance_request__{by}", "total"))
    keys = [r[by] for r in rows]
    if by == "supplier":
        names = {s.pk: (s.name, s.public_id) for s in Supplier.all_objects.filter(pk__in=keys)}
    elif by == "assigned_to":
        names = {u.pk: (str(u), u.public_id) for u in User.objects.filter(pk__in=keys)}
    elif by == "property":
        names = {p.pk: (p.name, p.public_id) for p in Property.all_objects.filter(pk__in=keys)}
    else:
        choices = MaintenanceRequest.Kind if by == "kind" else Priority
        names = {value: (str(label), value) for value, label in choices.choices}
    result = [_performance(r[by], *names.get(r[by], (str(r[by]), r[by])), r, reopened.get(r[by], 0),
                           costs.get(r[by], ZERO) if with_costs else None) for r in rows]
    if by == "priority":
        order = list(Priority.values)
        result.sort(key=lambda p: order.index(p.key) if p.key in order else len(order))
    elif by in GROUPS:
        result.sort(key=lambda p: (-p.jobs, p.name.lower()))
    else:
        result.sort(key=lambda p: (-(p.fix_on_time if p.fix_on_time is not None else -1), -p.jobs, p.name.lower()))
    return result


def overall(queryset, *, now: datetime.datetime | None = None, with_costs: bool = False) -> Performance:
    """Every request in the queryset added up into one row (D-110), cancelled ones left out."""
    now = now or timezone.now()
    qs = queryset.exclude(status=Status.CANCELLED)
    r = qs.annotate(responded=Coalesce("started_at", "done_at")).order_by().aggregate(**_measures(now))
    cost = (_approved_costs(qs).aggregate(s=Sum("amount"))["s"] or ZERO) if with_costs else None
    return _performance(None, _("All jobs"), None, r, _reopened(qs).count(), cost)


def open_on_unit(membership: Membership, unit: Unit):
    return visible_requests(membership).filter(unit=unit, status__in=MaintenanceRequest.UNFINISHED)


def cost_totals(queryset) -> dict[int, Decimal]:
    """Request pk → approved cost, for a list."""
    rows = (Expense.objects.filter(maintenance_request__in=queryset.values("pk"), status=Expense.Status.APPROVED)
            .values("maintenance_request_id").annotate(total=Sum("amount")))
    return {r["maintenance_request_id"]: r["total"] for r in rows}


# ---------------------------------------------------------------------------
# Notifications (D-068 item 8)
# ---------------------------------------------------------------------------


def _when(moment: datetime.datetime) -> str:
    return timezone.localtime(moment).strftime("%d/%m/%Y %H:%M")


def _notify_reported(req: MaintenanceRequest, reporter) -> None:
    who = str(reporter) if reporter is not None else _("%(tenant)s, from USSD") % {"tenant": req.tenant}
    context = {"number": req.number, "property": req.property.name, "unit": req.where, "title": req.title,
               "priority": req.get_priority_display(), "reported_by": who}
    for user in staff_with(req.organization, "maintenance.assign", req.property):
        if reporter is not None and user.pk == reporter.pk:
            continue
        notify(req.organization, "maintenance_reported", user=user, context=context, created_by=reporter,
               dedupe_key=f"maintenance_reported:{req.pk}:{user.pk}", urgent=req.priority == Priority.EMERGENCY)


LATE_WHAT = {
    "respond": gettext_lazy("not started"),
    "fix": gettext_lazy("not done"),
}


def _late_recipients(req: MaintenanceRequest) -> list:
    users = {u.pk: u for u in staff_with(req.organization, "maintenance.assign", req.property)}
    if req.assigned_to_id and req.assigned_to.is_active and req.assigned_to.pk not in users:
        m = Membership.objects.filter(organization_id=req.organization_id, user_id=req.assigned_to_id,
                                      is_active=True, archived_at__isnull=True).select_related(
            "user", "organization", "role").first()
        if m is not None and sees(m, req):
            users[m.user.pk] = m.user
    return list(users.values())


def send_late_alerts(now: datetime.datetime | None = None) -> dict[str, int]:
    """Once per request, target and time: to the assignee and `maintenance.assign` holders (D-074 item 7)."""
    now = now or timezone.now()
    counts = {"late_to_start": 0, "late_to_fix": 0, "late_alerts_sent": 0}
    base = MaintenanceRequest.objects.select_related("organization", "property", "unit", "assigned_to")
    for what, qs in (("respond", late_to_start(base, now)),
                     ("fix", base.filter(status__in=MaintenanceRequest.OPEN, due_at__lt=now))):
        for req in qs.order_by("pk"):
            counts["late_to_start" if what == "respond" else "late_to_fix"] += 1
            at = req.respond_by if what == "respond" else req.due_at
            context = {"number": req.number, "property": req.property.name, "unit": req.where, "title": req.title,
                       "priority": req.get_priority_display(), "what": str(LATE_WHAT[what]), "due": _when(at)}
            for user in _late_recipients(req):
                if notify(req.organization, "maintenance_late", user=user, context=context,
                          urgent=req.priority == Priority.EMERGENCY,
                          dedupe_key=f"maintenance_late:{req.pk}:{what}:{at.isoformat()}:{user.pk}"):
                    counts["late_alerts_sent"] += 1
    return counts


def _notify_assigned(req: MaintenanceRequest, user, by, update: MaintenanceUpdate) -> None:
    notify(req.organization, "maintenance_assigned", user=user, created_by=by,
           dedupe_key=f"maintenance_assigned:{update.pk}:{user.pk}", urgent=req.priority == Priority.EMERGENCY,
           context={"number": req.number, "property": req.property.name, "unit": req.where, "title": req.title,
                    "priority": req.get_priority_display(), "due": _when(req.due_at), "assigned_by": str(by)})


def _sms_supplier(req: MaintenanceRequest, supplier: Supplier, by, update: MaintenanceUpdate) -> None:
    try:
        phone = normalize_phone(supplier.phone)
    except InvalidPhoneNumber:
        return
    notify(req.organization, "maintenance_supplier_job", phone=phone, created_by=by,
           dedupe_key=f"maintenance_supplier_job:{update.pk}", urgent=req.priority == Priority.EMERGENCY,
           context={"supplier_name": supplier.name, "number": req.number, "property": req.property.name,
                    "unit": req.where, "title": req.title, "priority": req.get_priority_display(),
                    "assigned_by": str(by), "respond_by": _when(req.respond_by), "due": _when(req.due_at)})


TENANT_STATUS = {
    "received": gettext_lazy("received"),
    "done": gettext_lazy("fixed"),
    "cancelled": gettext_lazy("cancelled"),
}


def _tell_tenant(req: MaintenanceRequest, event: str, note: str, by) -> None:
    """One message per event (the dedupe key), so "fixed" goes once even after Done, Closed and a reopen."""
    tenant = req.tenant
    key = event.split(":")[0]
    with translation.override(tenant.language or "en"):
        status = str(TENANT_STATUS[key]) if key in TENANT_STATUS else req.get_status_display().lower()
        # WhatsApp templates cannot carry an empty field (D-044 item 16).
        note = note or "-"
        notify(req.organization, "maintenance_update", tenant=tenant, lease=req.lease, created_by=by,
               dedupe_key=f"maintenance_update:{req.pk}:{event}",
               context={"tenant_name": tenant.name, "unit": req.where, "property": req.property.name,
                        "pay_reference": "", "number": req.number, "title": req.title, "status": status,
                        "note": note[:300]})
