"""An agency's branches (D-040, D-127): adding, editing, archiving and restoring them.

A branch groups properties. Staff given a branch see every property in it, including ones added
later (`permissions.accessible_property_ids`). Managing branches needs `organization.manage`,
which only organization-wide members hold.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.db.models.functions import Upper
from django.utils import timezone
from django.utils.translation import gettext as _

from audit import services as audit
from core.phone import InvalidPhoneNumber, normalize_phone

from .models import Branch, Membership
from .permissions import require

FIELDS = ("name", "phone", "email")


def listing(org):
    """Every branch, archived last, with its live properties and live staff counted."""
    return (Branch.all_objects.for_org(org)
            .annotate(property_count=Count("properties", filter=Q(properties__archived_at__isnull=True),
                                           distinct=True),
                      member_count=Count("memberships", filter=Q(memberships__archived_at__isnull=True,
                                                                 memberships__is_active=True), distinct=True))
            .order_by(F("archived_at").asc(nulls_first=True), Upper("name")))


def _check(actor: Membership, branch: Branch | None = None) -> None:
    require(actor, "organization.manage")
    if branch is not None and branch.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))


def _clean(branch: Branch, fields: dict) -> None:
    errors = {}
    name = (fields.get("name") or "").strip()
    if not name:
        errors["name"] = _("Enter the branch name.")
    elif (Branch.all_objects.for_org(branch.organization).filter(name__iexact=name)
          .exclude(pk=branch.pk).exists()):
        errors["name"] = _("Another branch is already called %(name)s.") % {"name": name}
    phone = (fields.get("phone") or "").strip()
    if phone:
        try:
            phone = normalize_phone(phone)
        except InvalidPhoneNumber:
            errors["phone"] = _("Enter a valid Kenyan phone number.")
    if errors:
        raise ValidationError(errors)
    branch.name, branch.phone, branch.email = name, phone, (fields.get("email") or "").strip().lower()


def _snapshot(branch: Branch) -> dict:
    return {f: getattr(branch, f) for f in FIELDS}


@transaction.atomic
def create_branch(actor: Membership, *, request=None, **fields) -> Branch:
    _check(actor)
    branch = Branch(organization=actor.organization)
    _clean(branch, fields)
    try:
        with transaction.atomic():
            branch.save()
    except IntegrityError:
        raise ValidationError({"name": _("Another branch already has that name.")}) from None
    audit.record("branch.create", actor=actor.user, organization=actor.organization, obj=branch, request=request,
                 changes={k: [None, v] for k, v in _snapshot(branch).items() if v})
    return branch


@transaction.atomic
def update_branch(actor: Membership, branch: Branch, *, request=None, **fields) -> Branch:
    _check(actor, branch)
    before = _snapshot(branch)
    _clean(branch, fields)
    try:
        with transaction.atomic():
            branch.save()
    except IntegrityError:
        raise ValidationError({"name": _("Another branch already has that name.")}) from None
    changes = audit.diff(before, _snapshot(branch))
    if changes:
        audit.record("branch.update", actor=actor.user, organization=actor.organization, obj=branch,
                     request=request, changes=changes)
    return branch


@transaction.atomic
def archive_branch(actor: Membership, branch: Branch, *, request=None) -> Branch:
    """Only an empty branch can go: its staff would otherwise lose those properties without anyone noticing."""
    _check(actor, branch)
    branch = Branch.all_objects.select_for_update().get(pk=branch.pk)
    if branch.is_archived:
        return branch
    if branch.properties.filter(archived_at__isnull=True).exists():
        raise ValidationError(_("Move this branch's properties to another branch first."))
    branch.archived_at, branch.archived_by = timezone.now(), actor.user
    branch.save(update_fields=["archived_at", "archived_by", "updated_at"])
    audit.record("branch.archive", actor=actor.user, organization=actor.organization, obj=branch, request=request)
    return branch


@transaction.atomic
def restore_branch(actor: Membership, branch: Branch, *, request=None) -> Branch:
    _check(actor, branch)
    branch = Branch.all_objects.select_for_update().get(pk=branch.pk)
    if not branch.is_archived:
        return branch
    branch.archived_at, branch.archived_by = None, None
    branch.save(update_fields=["archived_at", "archived_by", "updated_at"])
    audit.record("branch.restore", actor=actor.user, organization=actor.organization, obj=branch, request=request)
    return branch
