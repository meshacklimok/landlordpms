"""Loads an organization written by ``org_dump`` into this database as a new organization.

    python manage.py org_load <file.zip> --dry-run     # does everything, then rolls back
    python manage.py org_load <file.zip>

Every row gets a new id here and its links are rewritten, so nothing already in this database is touched or
overwritten. Staff and other users are matched by phone number (an existing account is reused, with its own
password); a user not found is created with the password hash from the file. One transaction: any error and
nothing is saved. Old audit events are loaded without sending security alerts.
"""

import json
import secrets
import uuid
import zipfile

from django.apps import apps
from django.core import serializers
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models.signals import post_save

from accounts.models import Capability, Organization, RoleTemplate, User


class DryRun(Exception):
    pass


def _deserialize(row):
    return next(serializers.deserialize("json", json.dumps([row]), ignorenonexistent=True)).object


def _relations(model):
    return [f for f in model._meta.concrete_fields if f.is_relation and (f.many_to_one or f.one_to_one)]


def _order(labels: set[str]) -> list[str]:
    """Parents before children; a cycle is broken by whatever is left over (its links are fixed afterwards)."""
    deps = {}
    for label in labels:
        model = apps.get_model(label)
        deps[label] = {r.related_model._meta.label_lower for r in _relations(model)
                       if r.related_model._meta.label_lower in labels and r.related_model is not model}
    ordered, done = [], set()
    while deps:
        ready = sorted(label for label, need in deps.items() if need <= done)
        if not ready:
            def required(label):  # unmet parents this table cannot be saved without
                return sum(1 for r in _relations(apps.get_model(label)) if not r.null
                           and r.related_model._meta.label_lower in deps and r.related_model._meta.label_lower != label)
            ready = [min(sorted(deps), key=required)]
        for label in ready:
            ordered.append(label)
            done.add(label)
            del deps[label]
    return ordered


class Command(BaseCommand):
    help = "Load an org_dump file as a new organization."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, path, dry_run, **options):
        with zipfile.ZipFile(path) as zf:
            data = json.loads(zf.read("data.json"))
            stored = {n[len("files/"):]: n for n in zf.namelist() if n.startswith("files/")}
            from accounts import alerts
            post_save.disconnect(sender=apps.get_model("audit.AuditEvent"), dispatch_uid="accounts_alerts_audit")
            try:
                with transaction.atomic():
                    self._load(data, zf, stored, dry_run)
                    if dry_run:
                        raise DryRun
            except DryRun:
                self.stdout.write(self.style.WARNING("Dry run: everything above worked and was rolled back."))
            finally:
                post_save.connect(alerts._audited, sender=apps.get_model("audit.AuditEvent"),
                                  dispatch_uid="accounts_alerts_audit")

    def _load(self, data, zf, stored, dry_run):
        users = {}
        for row in data["users"]:
            existing = User.objects.filter(phone=row["fields"]["phone"]).first()
            if existing:
                users[row["pk"]] = existing.pk
                self.stdout.write(f"user {row['fields']['phone']}: already here (id {existing.pk}), reused")
                continue
            user = _deserialize(row)
            user.pk = None
            user._state.adding = True
            user.save_base(raw=True, force_insert=True)
            users[row["pk"]] = user.pk
            self.stdout.write(f"user {row['fields']['phone']}: created (id {user.pk})")

        shared = {}
        for label, model, column in (("accounts.capability", Capability, "codename"),
                                     ("accounts.roletemplate", RoleTemplate, "key")):
            here = dict(model.objects.values_list(column, "pk"))
            shared[label] = {int(old): here.get(name) for old, name in data.get("shared", {}).get(label, {}).items()}
        by_label = {}
        for row in data["objects"]:
            by_label.setdefault(row["model"], []).append(row)
        ids, pending, kept, counts = {}, [], {}, {}
        for label in _order(set(by_label)):
            model = apps.get_model(label)
            for row in by_label[label]:
                obj = _deserialize(row)
                old_pk = row["pk"]
                for field in _relations(model):
                    old = getattr(obj, field.attname)
                    if old is None:
                        continue
                    target = field.related_model
                    target_label = target._meta.label_lower
                    if target is User:
                        new = users.get(old)
                    elif target is Organization:
                        new = ids.get((target_label, old))
                    elif (target_label, old) in ids:
                        new = ids[(target_label, old)]
                    elif target_label in shared:
                        new = shared[target_label].get(old)
                        if new is None:
                            kept[f"{label}.{field.name} (no match here)"] = kept.get(f"{label}.{field.name} (no match here)", 0) + 1
                    elif target_label in by_label:  # parent of a cycle, not loaded yet
                        pending.append((model, obj, field.attname, target_label, old))
                        new = None
                    else:  # a table outside the organization (a shared catalogue): the same id
                        kept[f"{label}.{field.name}"] = kept.get(f"{label}.{field.name}", 0) + 1
                        continue
                    setattr(obj, field.attname, new)
                for field in model._meta.concrete_fields:  # the same file loaded twice, or a local copy of live data
                    if not field.unique or field.primary_key or not (field.name == "public_id" or "token" in field.name or "code" in field.name):
                        continue
                    if model._base_manager.filter(**{field.name: getattr(obj, field.name)}).exists():
                        fresh = uuid.uuid4() if field.name == "public_id" else secrets.token_urlsafe(16)
                        setattr(obj, field.name, fresh)
                if not model._meta.pk.is_relation:
                    obj.pk = None
                obj._state.adding = True
                obj.save_base(raw=True, force_insert=True)
                ids[(label, old_pk)] = obj.pk
                counts[label] = counts.get(label, 0) + 1
        for model, obj, attname, target_label, old in pending:
            new = ids.get((target_label, old))
            if new is not None:
                model.objects.filter(pk=obj.pk).update(**{attname: new})
        for label, count in sorted(counts.items()):
            self.stdout.write(f"{label}: {count}")
        for where, count in sorted(kept.items()):
            self.stdout.write(f"kept shared reference {where}: {count}")
        new_org = ids[("accounts.organization", data["organization"])]
        self.stdout.write(f"new organization id: {new_org}")
        from subscriptions.services import subscription_for

        subscription_for(Organization.objects.get(pk=new_org))  # a fresh trial; the old subscription is not copied
        if not dry_run:
            written = 0
            for name, member in stored.items():
                if not default_storage.exists(name):
                    default_storage.save(name, ContentFile(zf.read(member)))
                    written += 1
            self.stdout.write(f"files written: {written}")
        if not users:
            raise CommandError("The file has no users; nobody could sign in.")
