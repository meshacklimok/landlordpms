"""One organization's data as a ZIP: a CSV per table, its uploaded files and a manifest.

Used by the organization's own Settings page (``organization.export_data``) and by the console owner. The nightly
server backup (ops.backup) holds every organization at once; this is one organization on its own, for their records,
for moving to another system or for a request under the Data Protection Act.

Tables are found from the models, so a new app is included without listing it here: every model with a foreign key
(or one-to-one) to Organization, and every model one step below one of those (a report's lines, a request's photos).
Login secrets, hashed tokens and encrypted credentials are left out, column by column and whole tables of them.
"""

import datetime
import io
import json
import logging
import re
import tempfile
import zipfile

from django.apps import apps
from django.conf import settings
from django.core.files import File
from django.db import models, transaction
from django.db.models import F
from django.http import FileResponse, Http404
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from audit import services as audit
from core.exports import BOM, Writer
from notifications.providers.sms import send_sms

from .models import DataExport, Membership, Organization, User

logger = logging.getLogger(__name__)
STUCK_AFTER = datetime.timedelta(hours=2)  # a run killed half way is started again

# Whole tables never exported: login factors, one-time codes, the USSD PIN, browser push keys, API replay records,
# and the exports themselves.
SKIPPED_MODELS = frozenset({
    "accounts.totpdevice", "accounts.recoverycode", "accounts.otpcode", "portal.ussdpin",
    "notifications.pushsubscription", "api.idempotentrequest", "accounts.dataexport",
})
# Columns never exported: encrypted credentials, hashes and the tokens in links and webhooks.
SECRET_FIELD = re.compile(r"(_encrypted|_hash|_token|^token|^secret|^password)$|^password")
USER_FIELDS = ("id", "full_name", "phone", "email", "is_active", "date_joined")


def _org_path(model) -> str | None:
    """The lookup from this model to its organization, if it has a direct one."""
    for field in model._meta.concrete_fields:
        if field.is_relation and (field.many_to_one or field.one_to_one) and field.related_model is Organization:
            return field.name
    return None


def tables() -> list[tuple[type[models.Model], str]]:
    """Every exportable model with the lookup that selects one organization's rows."""
    direct = {}
    for model in apps.get_models():
        if model is Organization or model._meta.label_lower in SKIPPED_MODELS:
            continue
        if (path := _org_path(model)) is not None:
            direct[model] = path
    found = dict(direct)
    for model in apps.get_models():
        if model in found or model is Organization or model._meta.label_lower in SKIPPED_MODELS:
            continue
        for field in model._meta.concrete_fields:
            parent = field.related_model if field.is_relation and (field.many_to_one or field.one_to_one) else None
            if parent in direct and parent is not User:
                found[model] = f"{field.name}__{direct[parent]}"
                break
    return sorted(found.items(), key=lambda item: item[0]._meta.label_lower)


def _columns(model) -> list[models.Field]:
    return [f for f in model._meta.concrete_fields if not SECRET_FIELD.search(f.name)]


def _cell(field, obj):
    value = getattr(obj, field.attname)
    if isinstance(field, models.FileField):
        return value.name if value else ""
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, default=str)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _write_csv(zf, name: str, header: list[str], rows) -> int:
    buffer = io.StringIO()
    buffer.write(BOM)
    out = Writer(buffer)
    out.writerow(header)
    count = 0
    for row in rows:
        out.writerow(row)
        count += 1
    zf.writestr(name, buffer.getvalue())
    return count


def _copy_file(zf, fieldfile, copied: set[str], missing: list[str]) -> None:
    if not fieldfile or fieldfile.name in copied:
        return
    copied.add(fieldfile.name)
    try:
        with fieldfile.storage.open(fieldfile.name, "rb") as source:
            zf.writestr(f"files/{fieldfile.name}", source.read())
    except (FileNotFoundError, OSError):
        missing.append(fieldfile.name)


def _users(org) -> models.QuerySet:
    """Staff, tenants with a portal login and owners with an owner login: names and contacts, never passwords."""
    ids = set(Membership.objects.filter(organization=org).values_list("user_id", flat=True))
    for label in ("portal.TenantAccount", "owner_portal.OwnerAccount"):
        model = apps.get_model(label)
        if any(f.name == "user" for f in model._meta.concrete_fields):
            ids |= set(model.objects.filter(organization=org).values_list("user_id", flat=True))
    return User.objects.filter(pk__in=ids).order_by("pk")


def build(org: Organization):
    """Writes the ZIP to a temporary file and returns (file, manifest). The file is open at the start; close it."""
    target = tempfile.TemporaryFile()
    manifest = {"organization": org.display_name, "public_id": str(org.public_id),
                "exported_at": timezone.now().isoformat(), "tables": {}, "files": 0, "missing_files": []}
    copied: set[str] = set()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        org_fields = _columns(Organization)
        manifest["tables"]["accounts.organization"] = _write_csv(
            zf, "data/accounts/organization.csv", [f.attname for f in org_fields],
            [[_cell(f, org) for f in org_fields]])
        for f in org_fields:
            if isinstance(f, models.FileField):
                _copy_file(zf, getattr(org, f.name), copied, manifest["missing_files"])

        manifest["tables"]["accounts.user"] = _write_csv(
            zf, "data/accounts/user.csv", list(USER_FIELDS),
            ([_cell(User._meta.get_field(n), u) for n in USER_FIELDS] for u in _users(org)))

        for model, path in tables():
            fields = _columns(model)
            files = [f for f in fields if isinstance(f, models.FileField)]
            meta = model._meta

            def rows(model=model, path=path, fields=fields, files=files):
                for obj in model._base_manager.filter(**{path: org}).order_by("pk").iterator(chunk_size=2000):
                    for f in files:
                        _copy_file(zf, getattr(obj, f.name), copied, manifest["missing_files"])
                    yield [_cell(f, obj) for f in fields]

            manifest["tables"][meta.label_lower] = _write_csv(
                zf, f"data/{meta.app_label}/{meta.model_name}.csv", [f.attname for f in fields], rows())
        manifest["files"] = len(copied) - len(manifest["missing_files"])
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        zf.writestr("README.txt", README.format(org=org.display_name, when=timezone.localtime().strftime("%d %b %Y %H:%M")))
    target.seek(0)
    return target, manifest


def filename(org: Organization) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", org.display_name.lower()).strip("-") or "organization"
    return f"{slug}-data-{timezone.localdate():%Y%m%d}.zip"


# --- asked for on a page, made by the run_data_exports job, downloaded by the one who asked -------------------


def request_export(org: Organization, user, request, *, for_console: bool = False) -> tuple[DataExport, bool]:
    """Queues an export, or returns the one already waiting. The bool says whether a new one was queued.

    Audited when asked for (``organization.export_data``, which texts the managers, or ``console.organization_export``
    on the organization's own log), so the alert goes out before any data has left.
    """
    with transaction.atomic():
        Organization.objects.select_for_update().filter(pk=org.pk).first()
        waiting = DataExport.objects.filter(organization=org, requested_by=user, for_console=for_console,
                                            status__in=[DataExport.Status.PENDING, DataExport.Status.RUNNING]).first()
        if waiting is not None:
            return waiting, False
        export = DataExport.objects.create(organization=org, requested_by=user, for_console=for_console)
        audit.record("console.organization_export" if for_console else "organization.export_data",
                     actor=user, organization=org, obj=org, request=request)
    return export, True


def make(export: DataExport) -> DataExport:
    """Builds one export's ZIP, saves it and texts the person who asked. A failure is kept on the row."""
    export.status, export.started_at = DataExport.Status.RUNNING, timezone.now()
    export.save(update_fields=["status", "started_at", "updated_at"])
    try:
        target, manifest = build(export.organization)
        with target:
            export.file.save(f"{export.public_id}.zip", File(target), save=False)
        export.size = export.file.size
        export.rows, export.files = sum(manifest["tables"].values()), manifest["files"]
        export.status, export.ready_at = DataExport.Status.READY, timezone.now()
        export.expires_at = export.ready_at + datetime.timedelta(hours=settings.DATA_EXPORT_HOURS)
        export.save()
    except Exception as exc:
        logger.exception("Data export %s failed", export.pk)
        export.status, export.error = DataExport.Status.FAILED, f"{type(exc).__name__}: {exc}"[:2000]
        export.save(update_fields=["status", "error", "updated_at"])
        return export
    _tell(export)
    return export


def _tell(export: DataExport) -> None:
    phone = export.requested_by.phone
    if not phone:
        return
    link = settings.SITE_URL.rstrip("/") + page_url(export)
    try:
        send_sms(phone, _("The data of %(org)s is ready to download until %(until)s: %(link)s")
                 % {"org": export.organization.display_name,
                    "until": timezone.localtime(export.expires_at).strftime("%d %b %H:%M"), "link": link})
    except Exception:
        logger.exception("Could not text that data export %s is ready", export.pk)


def page_url(export: DataExport) -> str:
    """Where the person who asked sees the export: their Settings page, or the console organization page."""
    if export.for_console:
        return reverse("console:organization", args=[export.organization.public_id])
    return reverse("accounts:organization")


def run_due(limit: int = 5) -> dict:
    """The job: makes waiting exports (oldest first), restarts ones left running by a crash, deletes expired files."""
    now = timezone.now()
    stuck = DataExport.objects.filter(status=DataExport.Status.RUNNING, started_at__lt=now - STUCK_AFTER)
    restarted = stuck.update(status=DataExport.Status.PENDING)
    made = failed = 0
    for pk in DataExport.objects.filter(status=DataExport.Status.PENDING).order_by("created_at").values_list(
            "pk", flat=True)[:limit]:
        with transaction.atomic():
            export = (DataExport.objects.select_for_update(skip_locked=True).select_related("organization", "requested_by")
                      .filter(pk=pk, status=DataExport.Status.PENDING).first())
            if export is None:
                continue
            export.status, export.started_at = DataExport.Status.RUNNING, timezone.now()
            export.save(update_fields=["status", "started_at", "updated_at"])
        if make(export).status == DataExport.Status.READY:
            made += 1
        else:
            failed += 1
    return {"made": made, "failed": failed, "restarted": restarted, "expired": expire(now)}


def expire(now=None) -> int:
    """Deletes the files of exports past their time. The rows stay, as a record of who took what."""
    count = 0
    for export in DataExport.objects.filter(status=DataExport.Status.READY, expires_at__lte=now or timezone.now()):
        if export.file:
            export.file.delete(save=False)
        export.status = DataExport.Status.EXPIRED
        export.save(update_fields=["status", "file", "updated_at"])
        count += 1
    return count


def recent(org: Organization, user, *, for_console: bool = False):
    return DataExport.objects.filter(organization=org, requested_by=user, for_console=for_console)[:5]


def serve(export: DataExport, request) -> FileResponse:
    """The ZIP, to the one who asked, while it has not expired. Each download is audited."""
    if export.requested_by_id != request.user.pk or not export.can_download:
        raise Http404
    DataExport.objects.filter(pk=export.pk).update(downloads=F("downloads") + 1)
    audit.record("data_export.download", actor=request.user, organization=export.organization, obj=export.organization,
                 request=request, changes={"export": [None, str(export.public_id)]})
    return FileResponse(export.file.open("rb"), as_attachment=True, filename=filename(export.organization),
                        content_type="application/zip")


README = """All the data of {org} on NyumbaPMS, exported {when}.

data/<app>/<table>.csv  one file per table, UTF-8, first row the column names. Columns ending in _id point to
                        the "id" column of another table (created_by_id and similar point to data/accounts/user.csv).
files/                  the uploaded files (lease PDFs, receipts, photos, scans), at the path named in the tables.
manifest.json           how many rows each table has, and any file that could not be found.

Passwords, login codes, hashed tokens and encrypted payment credentials are not included.
Keep this file safe: it holds your tenants' personal data.
"""
