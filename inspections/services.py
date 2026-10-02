"""Condition reports and the unit item register (D-047). Views stay thin; the rules live here."""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import can, require, visible_properties
from audit import services as audit
from leases.models import Lease
from properties.models import Property, Unit

from . import photos
from .defaults import default_items
from .models import CONDITION_RANK, Condition, ConditionPhoto, ConditionReport, ConditionReportLine, UnitItem

Kind = ConditionReport.Kind
Status = ConditionReport.Status

MAX_PHOTOS_PER_LINE = 10
MAX_PHOTOS_PER_REPORT = 60

# Which lease statuses each kind of report may be started on (D-047 item 2).
START_STATUSES = {
    Kind.MOVE_IN: (Lease.Status.ACTIVE,),
    Kind.MOVE_OUT: (Lease.Status.ACTIVE, Lease.Status.ENDED, Lease.Status.TERMINATED),
}


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def visible_reports(membership: Membership, queryset=None):
    qs = (queryset if queryset is not None else ConditionReport.objects.all()).filter(
        organization=membership.organization)
    return qs.filter(unit__property__in=visible_properties(membership, Property.all_objects.all()))


def _check(actor: Membership, unit: Unit, capability: str = "inspections.record") -> None:
    if unit.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, capability, unit.property)


def _audit(action, actor, obj, request, changes=None):
    audit.record(action, actor=actor.user, organization=actor.organization, obj=obj, request=request,
                 changes=changes or {})


# ---------------------------------------------------------------------------
# Item register
# ---------------------------------------------------------------------------


def _next_order(unit: Unit) -> int:
    return (UnitItem.all_objects.filter(unit=unit).aggregate(m=Max("sort_order"))["m"] or 0) + 10


def ensure_register(unit: Unit, user=None) -> bool:
    """Fills a unit's register from the defaults when it has never had an item. True if it did."""
    if UnitItem.all_objects.filter(unit=unit).exists():
        return False
    UnitItem.objects.bulk_create([
        UnitItem(organization_id=unit.organization_id, unit=unit, area=area, name=name, sort_order=(i + 1) * 10,
                 created_by=user)
        for i, (area, name) in enumerate(default_items(unit.unit_type))])
    return True


@transaction.atomic
def fill_defaults(actor: Membership, unit: Unit, *, request=None) -> bool:
    _check(actor, unit)
    filled = ensure_register(unit, actor.user)
    if filled:
        _audit("inspections.register_defaults", actor, unit, request, {"unit_type": [None, unit.unit_type]})
    return filled


def _clean_item(area, name, quantity, notes) -> dict:
    name = (name or "").strip()[:100]
    if not name:
        raise ValidationError({"name": _("Name the item.")})
    try:
        quantity = int(quantity or 1)
    except (TypeError, ValueError):
        quantity = 0
    if not 1 <= quantity <= 999:
        raise ValidationError({"quantity": _("Enter a number from 1 to 999.")})
    return {"area": (area or "").strip()[:60], "name": name, "quantity": quantity, "notes": (notes or "").strip()[:200]}


@transaction.atomic
def add_item(actor: Membership, unit: Unit, *, name, area="", quantity=1, notes="", request=None) -> UnitItem:
    _check(actor, unit)
    if unit.is_archived:
        raise ValidationError(_("This unit is archived."))
    item = UnitItem.objects.create(organization_id=unit.organization_id, unit=unit, sort_order=_next_order(unit),
                                   created_by=actor.user, **_clean_item(area, name, quantity, notes))
    _audit("inspections.item_add", actor, unit, request, {"item": [None, str(item)]})
    return item


@transaction.atomic
def update_item(actor: Membership, item: UnitItem, *, name, area="", quantity=1, notes="", request=None) -> UnitItem:
    _check(actor, item.unit)
    before = str(item)
    for field, value in _clean_item(area, name, quantity, notes).items():
        setattr(item, field, value)
    item.save()
    _audit("inspections.item_edit", actor, item.unit, request, {"item": [before, str(item)]})
    return item


@transaction.atomic
def archive_item(actor: Membership, item: UnitItem, *, request=None) -> None:
    _check(actor, item.unit)
    if not item.is_archived:
        item.archive(actor.user)
        _audit("inspections.item_archive", actor, item.unit, request, {"item": [str(item), None]})


@transaction.atomic
def restore_item(actor: Membership, item: UnitItem, *, request=None) -> None:
    _check(actor, item.unit)
    if item.is_archived:
        item.restore()
        _audit("inspections.item_restore", actor, item.unit, request, {"item": [None, str(item)]})


# ---------------------------------------------------------------------------
# Starting, filling, completing and cancelling a report
# ---------------------------------------------------------------------------


def current(lease: Lease, kind: str) -> ConditionReport | None:
    """The report of this kind that is not cancelled, if any."""
    return ConditionReport.objects.filter(lease=lease, kind=kind).exclude(status=Status.CANCELLED).first()


def start_problem(lease: Lease, kind: str) -> str:
    """Why a report of this kind cannot be started on this lease, or "" if it can."""
    if lease.archived_at is not None:
        return _("This lease is archived.")
    if lease.status not in START_STATUSES[kind]:
        if kind == Kind.MOVE_IN:
            return _("A move-in report needs an active lease.")
        return _("A move-out report needs an active, ended or terminated lease.")
    if current(lease, kind) is not None:
        return _("This lease already has that report.")
    return ""


def _day(value) -> datetime.date:
    day = value or timezone.localdate()
    if day > timezone.localdate():
        raise ValidationError({"inspected_on": _("The date cannot be in the future.")})
    return day


@transaction.atomic
def start(actor: Membership, lease: Lease, kind: str, *, inspected_on=None, request=None) -> ConditionReport:
    if kind not in Kind.values:
        raise ValidationError(_("Choose move-in or move-out."))
    if lease.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    lease = Lease.all_objects.select_for_update().select_related("unit__property").get(pk=lease.pk)
    _check(actor, lease.unit)
    problem = start_problem(lease, kind)
    if problem:
        raise ValidationError(problem)
    ensure_register(lease.unit, actor.user)
    try:
        with transaction.atomic():
            report = ConditionReport.objects.create(
                organization=lease.organization, lease=lease, unit=lease.unit, kind=kind,
                inspected_on=_day(inspected_on), inspected_by=actor.user)
    except IntegrityError:
        raise ValidationError(_("This lease already has that report.")) from None
    ConditionReportLine.objects.bulk_create([
        ConditionReportLine(report=report, item=item, area=item.area, name=item.name, quantity=item.quantity,
                            sort_order=item.sort_order)
        for item in UnitItem.objects.filter(unit=lease.unit)])
    _audit("inspections.start", actor, report, request, {"kind": [None, kind], "lease": [None, lease.number]})
    return report


def _draft(actor: Membership, report: ConditionReport) -> ConditionReport:
    if report.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, "inspections.record", report.unit.property)
    report = ConditionReport.objects.select_for_update().get(pk=report.pk)
    if not report.is_draft:
        raise ValidationError(_("This report is %(status)s and can no longer be changed.")
                              % {"status": report.get_status_display().lower()})
    return report


@transaction.atomic
def save_details(actor: Membership, report: ConditionReport, *, inspected_on, tenant_present=False,
                 tenant_comments="", keys_handed=None, notes="", lines=None, request=None) -> ConditionReport:
    """Saves the draft. `lines` maps a line's pk to (condition, note); lines not given are left alone."""
    report = _draft(actor, report)
    report.inspected_on = _day(inspected_on)
    report.tenant_present = bool(tenant_present)
    report.tenant_comments = (tenant_comments or "").strip()[:2000]
    report.keys_handed = keys_handed
    report.notes = (notes or "").strip()[:4000]
    report.save()
    for line in report.lines.filter(pk__in=list(lines or {})):
        condition, note = lines[line.pk]
        if condition not in ("", *Condition.values):
            raise ValidationError(_("Choose a condition from the list."))
        note = (note or "").strip()[:300]
        if (line.condition, line.notes) != (condition, note):
            line.condition, line.notes = condition, note
            line.save(update_fields=["condition", "notes"])
    return report


@transaction.atomic
def add_line(actor: Membership, report: ConditionReport, *, name, area="", quantity=1, request=None):
    """An item found on the day: it joins the unit's register and this report."""
    report = _draft(actor, report)
    unit = report.unit
    item = UnitItem.objects.create(organization_id=unit.organization_id, unit=unit, sort_order=_next_order(unit),
                                   created_by=actor.user, **_clean_item(area, name, quantity, ""))
    line = ConditionReportLine.objects.create(report=report, item=item, area=item.area, name=item.name,
                                              quantity=item.quantity, sort_order=item.sort_order)
    _audit("inspections.item_add", actor, report, request, {"item": [None, str(item)]})
    return line


@transaction.atomic
def add_photo(actor: Membership, report: ConditionReport, upload, *, line=None, caption="",
              request=None) -> ConditionPhoto:
    report = _draft(actor, report)
    if line is not None and line.report_id != report.pk:
        raise ValidationError(_("That item is not on this report."))
    if report.photos.count() >= MAX_PHOTOS_PER_REPORT:
        raise ValidationError({"image": _("A report can have at most %(n)s photos.") % {"n": MAX_PHOTOS_PER_REPORT}})
    if line is not None and line.photos.count() >= MAX_PHOTOS_PER_LINE:
        raise ValidationError({"image": _("An item can have at most %(n)s photos.") % {"n": MAX_PHOTOS_PER_LINE}})
    content, width, height = photos.process(upload)
    photo = ConditionPhoto(report=report, line=line, width=width, height=height, caption=(caption or "").strip()[:200],
                           uploaded_by=actor.user)
    photo.image.save(content.name, content, save=False)
    photo.save()
    _audit("inspections.photo_add", actor, report, request, {"photo": [None, str(photo.public_id)],
                                                             "item": [None, str(line) if line else ""]})
    return photo


@transaction.atomic
def remove_photo(actor: Membership, photo: ConditionPhoto, *, request=None) -> None:
    report = _draft(actor, photo.report)
    name, storage = photo.image.name, photo.image.storage
    ConditionPhoto.objects.filter(pk=photo.pk).delete()
    transaction.on_commit(lambda: storage.delete(name))
    _audit("inspections.photo_remove", actor, report, request, {"photo": [str(photo.public_id), None]})


@transaction.atomic
def complete(actor: Membership, report: ConditionReport, *, request=None) -> ConditionReport:
    report = _draft(actor, report)
    missing = report.lines.filter(condition="").count()
    if missing:
        raise ValidationError(_("Give every item a condition first (%(n)s left). Use \"Not checked\" "
                                "for anything you could not see.") % {"n": missing})
    if not report.lines.exists():
        raise ValidationError(_("Add at least one item to the report."))
    report.status = Status.COMPLETED
    report.completed_at = timezone.now()
    report.completed_by = actor.user
    report.save(update_fields=["status", "completed_at", "completed_by", "updated_at"])
    _audit("inspections.complete", actor, report, request, {"status": [Status.DRAFT, Status.COMPLETED]})
    return report


@transaction.atomic
def cancel(actor: Membership, report: ConditionReport, *, reason: str, request=None) -> ConditionReport:
    if report.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, "inspections.record", report.unit.property)
    report = ConditionReport.objects.select_for_update().get(pk=report.pk)
    reason = (reason or "").strip()[:300]
    if not reason:
        raise ValidationError({"reason": _("Say why the report is being cancelled.")})
    if report.status == Status.CANCELLED:
        raise ValidationError(_("This report is already cancelled."))
    if report.deposit_entries.exists():
        raise ValidationError(_("A deposit deduction cites this report, so it cannot be cancelled."))
    before = report.status
    report.status = Status.CANCELLED
    report.cancelled_at, report.cancelled_by, report.cancel_reason = timezone.now(), actor.user, reason
    report.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason", "updated_at"])
    _audit("inspections.cancel", actor, report, request, {"status": [before, Status.CANCELLED],
                                                          "reason": [None, reason]})
    return report


# ---------------------------------------------------------------------------
# Move-out against move-in
# ---------------------------------------------------------------------------


def baseline(report: ConditionReport) -> ConditionReport | None:
    """The completed move-in report a move-out report is compared with (D-047 item 6).

    This lease's, or else the one of the lease it renewed, as long as that was on the same unit.
    """
    if report.kind != Kind.MOVE_OUT:
        return None
    lease, seen = report.lease, set()
    while lease is not None and lease.unit_id == report.unit_id and lease.pk not in seen:
        seen.add(lease.pk)
        found = (ConditionReport.objects.filter(lease=lease, kind=Kind.MOVE_IN, status=Status.COMPLETED)
                 .order_by("-inspected_on", "-pk").first())
        if found is not None:
            return found
        lease = lease.previous_lease
    return None


def is_worse(before: str, after: str) -> bool:
    return before in CONDITION_RANK and after in CONDITION_RANK and CONDITION_RANK[after] > CONDITION_RANK[before]


def rows(report: ConditionReport, before: ConditionReport | None = None) -> list[dict]:
    """Each line with its photos and, for a move-out, the move-in line for the same item."""
    lines = list(report.lines.prefetch_related("photos"))
    earlier = {}
    if before is not None:
        earlier = {line.item_id: line for line in before.lines.prefetch_related("photos")}
    out = []
    for line in lines:
        prev = earlier.get(line.item_id)
        out.append({"line": line, "before": prev, "photos": list(line.photos.all()),
                    "worse": bool(prev) and is_worse(prev.condition, line.condition)})
    return out


def deduction_reason(report: ConditionReport, table: list[dict] | None = None) -> str:
    """A suggested reason for a deposit deduction: the items found worse than at move-in."""
    table = table if table is not None else rows(report, baseline(report))
    worse = [r["line"] for r in table if r["worse"]]
    if not worse:
        worse = [r["line"] for r in table if r["line"].condition in (Condition.DAMAGED, Condition.MISSING)]
    if not worse:
        return ""
    items = "; ".join(f"{line} ({line.get_condition_display().lower()})" for line in worse)
    return (_("Move-out report %(date)s: %(items)s") % {"date": report.inspected_on.strftime("%d/%m/%Y"),
                                                         "items": items})[:300]


def can_deduct(membership: Membership, report: ConditionReport) -> bool:
    return (report.kind == Kind.MOVE_OUT and report.status == Status.COMPLETED
            and report.lease.archived_at is None and can(membership, "deposits.deduct", report.unit.property))


def deduct(actor: Membership, report: ConditionReport, *, amount, deposit_type, apply_to_balance=False,
           reason="", entry_date=None, request=None):
    """A deposit deduction that cites this report (D-047 item 7). The deposit rules still apply."""
    from billing import deposits

    return deposits.deduct(actor, report.lease, amount=amount, deposit_type=deposit_type, reason=reason,
                           entry_date=entry_date, apply_to_balance=apply_to_balance, condition_report=report,
                           request=request)
