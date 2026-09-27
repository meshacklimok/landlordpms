"""Bulk CSV import of units and tenants (doc 11 §20, doc 14 A2/B8).

Flow: upload -> preview -> apply -> (undo within 24 hours).
- The preview runs every row through the normal create services inside a transaction that is
  rolled back, so it reports exactly what applying would do, duplicates within the file included.
- Applying runs the rows again for real. Rows that fail are skipped and reported; the rest are kept.
- Undo deletes the records the batch created, except any already used on a lease.
- Codes and phones are normalised by the same services the forms use.
- A batch belongs to whoever uploaded it. Members with every property can also act on anyone's batch;
  members limited to some properties see only their own.
- A preview holds the raw file, ID numbers included, so it expires after PREVIEW_TTL and
  ``purge_stale_previews`` (run daily) empties it.
"""

import csv
import datetime
import io
import re
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import Membership
from accounts.permissions import can, require, visible_properties
from audit import services as audit
from core.phone import InvalidPhoneNumber, normalize_phone
from properties import services as property_services
from properties.models import Building, Property, Unit, clean_code
from tenants import services as tenant_services
from tenants.models import Tenant

from .models import ImportBatch

Kind = ImportBatch.Kind
Status = ImportBatch.Status

MAX_BYTES = 1_000_000
MAX_ROWS = 2000
PREVIEW_TTL = datetime.timedelta(hours=24)

COLUMNS = {
    Kind.UNITS: ("property_code", "unit_code", "building", "unit_type", "type_label", "list_rent"),
    Kind.TENANTS: ("name", "phone", "alt_phone", "email", "kind", "contact_person", "id_type", "id_number",
                   "kra_pin", "emergency_contact_name", "emergency_contact_phone", "notes"),
}
REQUIRED = {Kind.UNITS: ("property_code", "unit_code"), Kind.TENANTS: ("name", "phone")}
EXAMPLES = {
    Kind.UNITS: ("GV", "A1", "Block A", "APARTMENT", "2 bedroom", "15000"),
    Kind.TENANTS: ("Wanjiku Kamau", "0712345678", "", "wanjiku@example.com", "INDIVIDUAL", "", "NATIONAL_ID",
                   "12345678", "A123456789B", "", "", ""),
}
ALIASES = {
    "property": "property_code", "unit": "unit_code", "code": "unit_code", "type": "unit_type",
    "label": "type_label", "rent": "list_rent", "asking_rent": "list_rent",
    "tenant": "name", "tenant_name": "name", "full_name": "name", "phone_number": "phone", "mobile": "phone",
    "kra": "kra_pin", "id": "id_number", "id_no": "id_number",
}
CAPABILITY = {Kind.UNITS: "units.manage", Kind.TENANTS: "tenants.manage"}
SENSITIVE = set(Tenant.SENSITIVE_FIELDS)


def can_import(membership: Membership, kind: str) -> bool:
    return can(membership, CAPABILITY[kind])


def visible_batches(membership: Membership, queryset=None):
    """Batches this membership may see: its own, or everyone's with access to every property."""
    qs = (queryset if queryset is not None else ImportBatch.objects.all()).filter(
        organization=membership.organization, kind__in=[k for k in Kind.values if can_import(membership, k)])
    return qs if membership.all_properties else qs.filter(created_by_id=membership.user_id)


def can_access(membership: Membership, batch: ImportBatch) -> bool:
    return (batch.organization_id == membership.organization_id and can_import(membership, batch.kind)
            and (membership.all_properties or batch.created_by_id == membership.user_id))


def is_expired(batch: ImportBatch) -> bool:
    return batch.status == Status.PREVIEW and timezone.now() >= batch.created_at + PREVIEW_TTL


def purge_stale_previews() -> int:
    """Discards previews older than PREVIEW_TTL and drops their rows. Returns how many."""
    return ImportBatch.objects.filter(status=Status.PREVIEW, created_at__lt=timezone.now() - PREVIEW_TTL).update(
        status=Status.DISCARDED, rows=[], updated_at=timezone.now())


def template_csv(kind: str) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS[kind])
    writer.writerow(EXAMPLES[kind])
    return out.getvalue()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _header(name: str) -> str:
    key = re.sub(r"[\s\-/]+", "_", name.strip().lower())
    return ALIASES.get(key, key)


def parse_csv(kind: str, upload) -> list[dict]:
    """Rows as [{"line", "data", "errors"}]. Problems with the file as a whole raise ValidationError."""
    raw = upload.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValidationError(_("The file is too big. Split it into files under 1 MB."))
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")  # Excel's "CSV" on Windows
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValidationError(_("The file is empty.")) from None
    except csv.Error:
        raise ValidationError(_("This does not look like a CSV file.")) from None
    names = [_header(h) for h in header]
    unknown = [h for h, n in zip(header, names, strict=True) if n and n not in COLUMNS[kind]]
    if unknown:
        raise ValidationError(_("Unknown columns: %(cols)s. Use the template's column names.")
                              % {"cols": ", ".join(unknown)})
    missing = [c for c in REQUIRED[kind] if c not in names]
    if missing:
        raise ValidationError(_("Missing columns: %(cols)s.") % {"cols": ", ".join(missing)})
    rows = []
    try:
        for record in reader:
            if not any(v.strip() for v in record):
                continue
            if len(rows) == MAX_ROWS:
                raise ValidationError(_("Import at most %(n)s rows at a time.") % {"n": MAX_ROWS})
            data = {n: v.strip() for n, v in zip(names, record, strict=False) if n}
            errors = [_("%(col)s is required.") % {"col": c} for c in REQUIRED[kind] if not data.get(c)]
            rows.append({"line": reader.line_num, "data": data, "errors": errors})
    except csv.Error:
        raise ValidationError(_("This does not look like a CSV file.")) from None
    if not rows:
        raise ValidationError(_("The file has no rows to import."))
    return rows


def _choice(choices, value: str, default=""):
    """Matches a choice by value or label, ignoring case, spaces and hyphens."""
    if not value:
        return default
    key = re.sub(r"[\s\-]+", "_", value.strip()).upper()
    for v, label in choices.choices:
        if key in (v, re.sub(r"[\s\-]+", "_", str(label)).upper()):
            return v
    raise ValidationError(_("“%(value)s” is not a valid choice.") % {"value": value})


def _money(value: str):
    if not value:
        return None
    cleaned = re.sub(r"(?i)kes|ksh|[,\s]", "", value)
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise ValidationError(_("“%(value)s” is not an amount.") % {"value": value}) from None
    if amount < 0:
        raise ValidationError(_("The amount cannot be negative."))
    return amount


# ---------------------------------------------------------------------------
# One row
# ---------------------------------------------------------------------------


def _create_unit(actor: Membership, data: dict, request):
    code = clean_code(data["property_code"])
    prop = visible_properties(actor, Property.objects.all()).filter(code__iexact=code).first()
    if prop is None:
        raise ValidationError(_("No property with code %(code)s.") % {"code": code})
    if not can(actor, "units.manage", prop):
        raise ValidationError(_("You cannot add units to %(name)s.") % {"name": prop.name})
    building = None
    if data.get("building"):
        building = Building.objects.filter(property=prop, name__iexact=data["building"]).first()
        if building is None:
            raise ValidationError(_("%(name)s has no building called “%(b)s”. Add it first.")
                                  % {"name": prop.name, "b": data["building"]})
    unit = property_services.create_unit(
        actor, prop, code=data["unit_code"], building=building, request=request,
        unit_type=_choice(Unit.Type, data.get("unit_type", ""), Unit.Type.APARTMENT),
        type_label=data.get("type_label", ""), list_rent=_money(data.get("list_rent", "")),
    )
    return unit, unit.payment_reference


def _create_tenant(actor: Membership, data: dict, request):
    if any(data.get(f) for f in SENSITIVE) and not can(actor, "tenants.view_sensitive"):
        raise ValidationError(_("You may not import ID numbers or KRA PINs. Leave those columns empty."))
    try:
        phone = normalize_phone(data["phone"])
    except InvalidPhoneNumber as exc:
        raise ValidationError(str(exc)) from None
    if tenant_services.tenants_with_phone(actor.organization, phone).exists():
        raise ValidationError(_("A tenant with phone %(phone)s already exists.") % {"phone": phone})
    fields = {k: data.get(k, "") for k in COLUMNS[Kind.TENANTS] if k not in ("name", "phone", "kind", "id_type")}
    tenant = tenant_services.create_tenant(
        actor, name=data["name"], phone=phone, request=request,
        kind=_choice(Tenant.Kind, data.get("kind", ""), Tenant.Kind.INDIVIDUAL),
        id_type=_choice(Tenant.IdType, data.get("id_type", "")), **fields,
    )
    return tenant, tenant.name


CREATE = {Kind.UNITS: _create_unit, Kind.TENANTS: _create_tenant}
MODEL = {Kind.UNITS: Unit, Kind.TENANTS: Tenant}


def _messages(exc: ValidationError) -> list[str]:
    if not hasattr(exc, "error_dict"):
        return exc.messages
    return [m if field == "__all__" else f"{field.replace('_', ' ')}: {m}"
            for field, msgs in exc.message_dict.items() for m in msgs]


def _process(actor: Membership, kind: str, rows: list[dict], request) -> tuple[list[dict], list[int]]:
    checked, created = [], []
    for row in rows:
        errors, label = list(row.get("parse_errors", row["errors"])), ""
        if not errors:
            try:
                with transaction.atomic():
                    obj, label = CREATE[kind](actor, row["data"], request)
            except ValidationError as exc:
                errors = _messages(exc)
            except PermissionDenied:
                errors = [_("You are not allowed to add this.")]
            else:
                created.append(obj.pk)
        checked.append({**row, "errors": errors, "label": label})
    return checked, created


def _counts(rows):
    ok = sum(1 for r in rows if not r["errors"])
    return ok, len(rows) - ok


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def preview_import(actor: Membership, kind: str, upload, request=None) -> ImportBatch:
    """Checks every row without keeping anything, and stores the result as a PREVIEW batch."""
    require(actor, CAPABILITY[kind])
    rows = parse_csv(kind, upload)
    for row in rows:
        row["parse_errors"] = row["errors"]
    with transaction.atomic():
        checked, _created = _process(actor, kind, rows, None)
        transaction.set_rollback(True)
    ok, bad = _counts(checked)
    return ImportBatch.objects.create(
        organization=actor.organization, kind=kind, created_by=actor.user,
        file_name=getattr(upload, "name", "")[:200], rows=checked, ok_count=ok, error_count=bad,
    )


def _locked(actor: Membership, batch: ImportBatch) -> ImportBatch:
    if batch.organization_id != actor.organization_id:
        raise PermissionDenied(_("That record belongs to another organization."))
    require(actor, CAPABILITY[batch.kind])
    if not can_access(actor, batch):
        raise PermissionDenied(_("Only the person who uploaded this import can use it."))
    return ImportBatch.objects.select_for_update().get(pk=batch.pk)


def _scrub(rows):
    return [{**r, "data": {k: v for k, v in r["data"].items() if k not in SENSITIVE}} for r in rows]


@transaction.atomic
def apply_import(actor: Membership, batch: ImportBatch, request=None) -> ImportBatch:
    locked = _locked(actor, batch)
    if locked.status != Status.PREVIEW:
        raise ValidationError(_("This import has already been applied or discarded."))
    if is_expired(locked):
        raise ValidationError(_("This preview is more than a day old. Upload the file again."))
    checked, created = _process(actor, locked.kind, locked.rows, request)
    ok, bad = _counts(checked)
    locked.rows, locked.created_ids, locked.ok_count, locked.error_count = _scrub(checked), created, ok, bad
    locked.status, locked.applied_at = Status.APPLIED, timezone.now()
    locked.save()
    audit.record("import.apply", actor=actor.user, organization=actor.organization, obj=locked, request=request,
                 changes={"kind": [None, locked.kind], "created": [None, ok], "skipped": [None, bad]})
    return locked


@transaction.atomic
def discard_import(actor: Membership, batch: ImportBatch) -> ImportBatch:
    locked = _locked(actor, batch)
    if locked.status != Status.PREVIEW:
        raise ValidationError(_("Only an import that has not been applied can be discarded."))
    locked.status, locked.rows = Status.DISCARDED, []
    locked.save(update_fields=["status", "rows", "updated_at"])
    return locked


def _in_use(kind: str, obj) -> bool:
    return obj.leases.exists() if kind == Kind.UNITS else obj.lease_links.exists()


@transaction.atomic
def undo_import(actor: Membership, batch: ImportBatch, request=None) -> tuple[list[str], list[str]]:
    """Deletes what the batch created, keeping anything already used. Returns (removed, kept) labels."""
    locked = _locked(actor, batch)
    if not locked.can_undo:
        raise ValidationError(_("An import can only be undone within 24 hours of applying it."))
    model = MODEL[locked.kind]
    removed, kept = [], []
    visible = set(tenant_services.visible_tenants(actor, Tenant.all_objects.filter(pk__in=locked.created_ids))
                  .values_list("pk", flat=True)) if locked.kind == Kind.TENANTS else set()
    for obj in model.all_objects.for_org(actor.organization).filter(pk__in=locked.created_ids):
        label = obj.payment_reference if locked.kind == Kind.UNITS else obj.name
        if locked.kind == Kind.UNITS and not can(actor, "units.manage", obj.property):
            kept.append(label)
            continue
        if locked.kind == Kind.TENANTS and obj.pk not in visible:
            kept.append(label)
            continue
        if _in_use(locked.kind, obj):
            kept.append(label)
            continue
        try:
            with transaction.atomic():
                obj.delete()
        except ProtectedError:
            kept.append(label)
        else:
            removed.append(label)
    locked.status, locked.undone_at = Status.UNDONE, timezone.now()
    locked.save(update_fields=["status", "undone_at", "updated_at"])
    audit.record("import.undo", actor=actor.user, organization=actor.organization, obj=locked, request=request,
                 changes={"removed": [None, removed], "kept": [None, kept]})
    return removed, kept
