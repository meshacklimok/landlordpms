"""Adding people to the developer console and choosing what they see (D-135)."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from accounts.models import User
from audit import services as audit

from . import access
from .models import PlatformStaff


def _check_grant(actor, role: str, capabilities: list[str]) -> list[str]:
    """What the seat gets: the ticked pages, or the role's usual ones when none are ticked.
    Nobody can give more than they hold, so a team manager cannot make an owner."""
    mine = set(access.allowed(actor))
    if "staff" not in mine:
        raise PermissionDenied("staff")
    chosen = [c for c in (capabilities or access.ROLE_PRESETS[role]) if c in access.CAPABILITIES]
    if not set(chosen) <= mine:
        raise ValidationError(_("You can only give access to pages you can open yourself."))
    if role == PlatformStaff.Role.OWNER and not actor.is_superuser:
        raise ValidationError(_("Only an owner can make another owner."))
    return chosen


@transaction.atomic
def add(actor, *, phone: str, role: str, capabilities: list[str], note: str = "", request=None) -> PlatformStaff:
    chosen = _check_grant(actor, role, capabilities)
    user = User.objects.filter(phone=phone, is_active=True).first()
    if user is None:
        raise ValidationError(_("No one has signed up with that number yet. Ask them to register first."))
    if PlatformStaff.objects.filter(user=user).exists():
        raise ValidationError(_("They are already on the console team."))
    seat = PlatformStaff.objects.create(user=user, role=role, capabilities=chosen, note=note, added_by=actor)
    audit.record("console.staff_added", actor=actor, obj=seat, request=request,
                 changes={"role": [None, role], "capabilities": [None, chosen]})
    return seat


@transaction.atomic
def update(actor, seat: PlatformStaff, *, role: str, capabilities: list[str], note: str = "", is_active: bool = True,
           request=None) -> PlatformStaff:
    if seat.user_id == actor.pk:
        raise ValidationError(_("You cannot change your own access. Ask another owner."))
    chosen = _check_grant(actor, role, capabilities)
    before = {"role": seat.role, "capabilities": seat.capabilities, "is_active": seat.is_active}
    seat.role, seat.capabilities, seat.note, seat.is_active = role, chosen, note, is_active
    seat.save(update_fields=["role", "capabilities", "note", "is_active", "updated_at"])
    audit.record("console.staff_changed", actor=actor, obj=seat, request=request,
                 changes={k: [before[k], getattr(seat, k)] for k in before if before[k] != getattr(seat, k)})
    return seat


# --- Owner-only controls (D-137): only superusers reach these; every one is audited with a reason. ---

def _owner_only(actor):
    if not actor.is_superuser:
        raise PermissionDenied("owner")


def _reason(reason: str) -> str:
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError(_("Write the reason, so the audit trail says why."))
    return reason[:200]


def set_organization_status(actor, org, status: str, reason: str, request=None):
    from accounts.models import Organization

    _owner_only(actor)
    reason = _reason(reason)
    if status not in Organization.Status.values:
        raise ValidationError(_("Unknown status."))
    before = org.status
    if before == status:
        return org
    org.status = status
    org.save(update_fields=["status", "updated_at"])
    audit.record("console.organization_status", actor=actor, organization=org, obj=org,
                 changes={"status": [before, status], "reason": [None, reason]}, request=request)
    return org


def extend_trial(actor, org, days: int, reason: str, request=None):
    import datetime

    from django.utils import timezone

    _owner_only(actor)
    reason = _reason(reason)
    sub = getattr(org, "subscription", None)
    if sub is None:
        raise ValidationError(_("This organization has no subscription."))
    if not 1 <= days <= 90:
        raise ValidationError(_("Choose between 1 and 90 days."))
    before = sub.trial_ends_on
    base = max(before or timezone.localdate(), timezone.localdate())
    sub.trial_ends_on = base + datetime.timedelta(days=days)
    sub.save(update_fields=["trial_ends_on", "updated_at"])
    audit.record("console.trial_extended", actor=actor, organization=org, obj=sub,
                 changes={"trial_ends_on": [str(before), str(sub.trial_ends_on)], "reason": [None, reason]},
                 request=request)
    return sub


def announce(actor, *, message: str, level: str, ends_at=None, request=None):
    from .models import Announcement

    _owner_only(actor)
    item = Announcement.objects.create(message=message.strip(), level=level, ends_at=ends_at, created_by=actor)
    audit.record("console.announcement", actor=actor, obj=item, changes={"message": [None, item.message]},
                 request=request)
    return item


def switch_announcement(actor, item, active: bool, request=None):
    _owner_only(actor)
    item.is_active = active
    item.save(update_fields=["is_active", "updated_at"])
    audit.record("console.announcement_on" if active else "console.announcement_off", actor=actor, obj=item,
                 request=request)
    return item


# --- The developer's expense ledger (D-138) ---

def save_expense(actor, form, request=None):
    """Saves a valid ExpenseForm (new or edited) and audits it."""
    from .models import PlatformExpense

    if "finance" not in access.allowed(actor):
        raise PermissionDenied("finance")
    new = form.instance.pk is None
    before = {}
    if not new:
        row = PlatformExpense.objects.filter(pk=form.instance.pk).values().first()
        before = {k: str(v) for k, v in row.items() if k in form.fields}
    expense = form.save(commit=False)
    if new:
        expense.recorded_by = actor
    expense.save()
    after = {k: str(getattr(expense, k)) for k in form.fields}
    audit.record("console.expense_added" if new else "console.expense_changed", actor=actor, obj=expense,
                 changes={k: [before.get(k), v] for k, v in after.items() if before.get(k) != v}, request=request)
    return expense


def delete_expense(actor, expense, request=None):
    if "finance" not in access.allowed(actor):
        raise PermissionDenied("finance")
    audit.record("console.expense_deleted", actor=actor, obj=expense,
                 changes={"amount": [str(expense.amount), None], "category": [expense.category, None]}, request=request)
    expense.delete()
