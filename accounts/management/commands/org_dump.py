"""Writes one organization, with its uploaded files, to a ZIP that ``org_load`` can put on another server.

    python manage.py org_dump <organization id> <file.zip>

For moving one organization between databases whose ids differ (a local copy to the live server). Payment
credentials, STK/Paybill rows, pay links and platform subscription rows are left out: the new server has its own.
The file holds staff password hashes, so keep it private and delete it after loading.
"""

import json
import re
import zipfile

from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import models

from accounts.data_export import tables
from accounts.models import Capability, Organization, RoleTemplate, User

# Blanked columns: encrypted values, hashes and passwords. Plain link tokens (QR codes, receipt links) are kept.
SECRET_FIELD = re.compile(r"(_encrypted|_hash)$|^password")
SKIP = frozenset({
    "notifications.whatsappaccount",
    "mpesa.darajacredentials", "mpesa.stkrequest", "mpesa.mpesatransaction", "mpesa.paylink",
    "subscriptions.subscription", "subscriptions.subscriptioninvoice", "subscriptions.subscriptionpayment",
    "subscriptions.platformcheckout", "subscriptions.platformreceipt", "subscriptions.platformtransaction", "growth.referralcode", "support.appopen",
})


class Command(BaseCommand):
    help = "Dump one organization (rows and files) to a ZIP for org_load."

    def add_arguments(self, parser):
        parser.add_argument("organization_id", type=int)
        parser.add_argument("path")

    def handle(self, *args, organization_id, path, **options):
        org = Organization.objects.filter(pk=organization_id).first()
        if org is None:
            raise CommandError(f"No organization {organization_id}.")
        objects, files, user_ids = [], set(), set()
        counts = {}

        def add(queryset):
            for obj in queryset.order_by("pk"):
                row = json.loads(serializers.serialize("json", [obj]))[0]
                for field in obj._meta.concrete_fields:
                    if SECRET_FIELD.search(field.name):
                        row["fields"][field.name] = "" if field.get_internal_type() != "BinaryField" else None
                    elif isinstance(field, models.FileField) and getattr(obj, field.name):
                        files.add(getattr(obj, field.name).name)
                    elif field.is_relation and field.related_model is User and getattr(obj, field.attname):
                        user_ids.add(getattr(obj, field.attname))
                objects.append(row)
                label = obj._meta.label_lower
                counts[label] = counts.get(label, 0) + 1

        add(Organization.objects.filter(pk=org.pk))
        for model, lookup in tables():
            if model._meta.label_lower in SKIP or model is User:
                continue
            if any(SECRET_FIELD.search(f.name) and f.unique for f in model._meta.concrete_fields):
                self.stdout.write(f"skipped {model._meta.label_lower} (one-time links and tokens)")
                continue
            add(model._base_manager.filter(**{lookup: org}))
        users = [json.loads(serializers.serialize("json", [u]))[0] for u in User.objects.filter(pk__in=user_ids)]
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("data.json", json.dumps({
                "organization": org.pk, "users": users, "objects": objects,
                # shared catalogues are matched by name on the other server, not by id
                "shared": {"accounts.capability": dict(Capability.objects.values_list("pk", "codename")),
                           "accounts.roletemplate": dict(RoleTemplate.objects.values_list("pk", "key"))}}))
            missing = []
            for name in sorted(files):
                try:
                    with open(_media_path(name), "rb") as handle:
                        zf.writestr(f"files/{name}", handle.read())
                except OSError:
                    missing.append(name)
        for label, count in sorted(counts.items()):
            self.stdout.write(f"{label}: {count}")
        self.stdout.write(f"users: {len(users)}  files: {len(files) - len(missing)}  missing files: {len(missing)}")
        self.stdout.write(self.style.SUCCESS(f"Wrote {path}"))


def _media_path(name: str) -> str:
    from django.core.files.storage import default_storage
    return default_storage.path(name)
