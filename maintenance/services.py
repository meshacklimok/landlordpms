"""Maintenance requests and jobs (D-068). Views stay thin; the rules live here."""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from accounts.models import Membership
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

from .models import MaintenancePhoto, MaintenanceRequest, MaintenanceUpdate

Status = MaintenanceRequest.Status
Priority = MaintenanceRequest.Priority
Kind = MaintenanceUpdate.Kind

MAX_PHOTOS = 10
MAX_PORTAL_PHOTOS = 3
MAX_PORTAL_PER_DAY = 5
STATS_DAYS = 90
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


def _create(org, prop, unit, user, fields, *, lease, tenant, photos, source, now, request) -> MaintenanceRequest:
    now = now or timezone.now()
    req = MaintenanceRequest.objects.create(
        organization=org, property=prop, unit=unit, lease=lease, tenant=tenant, reported_by=user, source=source,
        due_at=now + MaintenanceRequest.DUE_AFTER[fields["priority"]],
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


@transaction.atomic
def set_priority(actor: Membership, req: MaintenanceRequest, *, priority: str, due_at=None,
                 now: datetime.datetime | None = None, request=None) -> MaintenanceRequest:
    """A new priority resets the due time from now, unless a due time is given (D-068 item 2)."""
    req = _lock(req)
    _require(actor, req, "maintenance.assign")
    if req.status not in MaintenanceRequest.OPEN:
        raise ValidationError(_("Only an open request can be changed."))
    if priority not in Priority.values:
        raise ValidationError({"priority": _("Choose how urgent it is.")})
    now = now or timezone.now()
    if due_at is None:
        due_at = now + MaintenanceRequest.DUE_AFTER[priority] if priority != req.priority else req.due_at
    changes = {}
    if priority != req.priority:
        changes["priority"] = [req.priority, priority]
    if due_at != req.due_at:
        changes["due_at"] = [req.due_at.isoformat(), due_at.isoformat()]
    if not changes:
        return req
    req.priority, req.due_at = priority, due_at
    req.save(update_fields=["priority", "due_at", "updated_at"])
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


@dataclass
class Summary:
    open: int
    overdue: int
    closed_recently: int
    avg_days_to_close: float | None


def summary(queryset, now: datetime.datetime | None = None) -> Summary:
    now = now or timezone.now()
    agg = queryset.aggregate(
        open=Count("pk", filter=Q(status__in=MaintenanceRequest.OPEN)),
        overdue=Count("pk", filter=Q(status__in=MaintenanceRequest.OPEN, due_at__lt=now)))
    closed = queryset.filter(status=Status.CLOSED, closed_at__gte=now - datetime.timedelta(days=STATS_DAYS))
    stats = closed.aggregate(n=Count("pk"), avg=Avg(ExpressionWrapper(F("closed_at") - F("created_at"),
                                                                      output_field=DurationField())))
    avg = round(stats["avg"].total_seconds() / 86400, 1) if stats["avg"] is not None else None
    return Summary(open=agg["open"], overdue=agg["overdue"], closed_recently=stats["n"], avg_days_to_close=avg)


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
    context = {"number": req.number, "property": req.property.name, "unit": req.where, "title": req.title,
               "priority": req.get_priority_display(), "reported_by": str(reporter)}
    for user in staff_with(req.organization, "maintenance.assign", req.property):
        if user.pk == reporter.pk:
            continue
        notify(req.organization, "maintenance_reported", user=user, context=context, created_by=reporter,
               dedupe_key=f"maintenance_reported:{req.pk}:{user.pk}", urgent=req.priority == Priority.EMERGENCY)


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
                    "assigned_by": str(by)})


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
