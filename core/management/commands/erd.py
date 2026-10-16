"""Write config/docs/erd.md from the models, so the diagram is always the code's (D-103)."""

import difflib
import re
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import models

DOC = Path(settings.BASE_DIR) / "config" / "docs" / "erd.md"

SECTIONS = [
    ("Identity, organizations, roles, audit", ["accounts", "audit", "core"]),
    ("Properties, tenants, leases", ["properties", "tenants", "leases", "inspections", "documents", "prospects",
                                     "letters", "esign", "imports"]),
    ("Billing, payments, M-Pesa, bank, meters", ["billing", "payments", "mpesa", "banking", "meters"]),
    ("Expenses, repairs, owner reports", ["expenses", "maintenance", "reports"]),
    ("Messages, tenant and owner portals, assistant, support", ["notifications", "portal", "owner_portal",
                                                                "ai_assistant", "support"]),
    ("Platform: plans, the API, growth and the console", ["subscriptions", "api", "growth", "analytics"]),
]
# Apps whose links to a user are the point of the table (who belongs, who signs in).
USER_APPS = {"accounts", "portal", "owner_portal"}

HEADER = """# Entity Relationship Diagram

Generated from the models by `python manage.py erd`; do not edit by hand (D-103). A test fails when the models
change and this file is not regenerated. View with any Mermaid renderer (VS Code Markdown Preview Mermaid, GitHub).

Left out to keep the diagrams readable: every table's link to `ORGANIZATION` (almost all have one), and links to
`USER` saying who created, approved or was given something; sections 1 and 5 show who belongs and who signs in.
A link `A }o--|| B : "field"` means many A rows point at one B through `A.field`; `o|` on the B side means it may
be empty.
"""


def entity(model) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", model._meta.object_name).upper()


def links(model) -> list[str]:
    lines = []
    user_model = apps.get_model(settings.AUTH_USER_MODEL)
    for field in model._meta.get_fields():
        if not (field.is_relation and field.concrete) or field.auto_created:
            continue
        target = field.related_model
        if field.name == "organization" or target is None:
            continue
        if target is user_model and (model._meta.app_label not in USER_APPS or field.name.endswith("_by")):
            continue
        if isinstance(field, models.ManyToManyField):
            shape = "}o--o{"
        elif isinstance(field, models.OneToOneField):
            shape = "|o--o|" if field.null else "|o--||"
        else:
            shape = "}o--o|" if field.null else "}o--||"
        lines.append(f'    {entity(model)} {shape} {entity(target)} : "{field.name}"')
    return lines


def render() -> str:
    parts = [HEADER]
    placed = set()
    for number, (title, app_labels) in enumerate(SECTIONS, 1):
        chosen = [m for m in apps.get_models() if m._meta.app_label in app_labels]
        placed.update(chosen)
        names = ", ".join(sorted(entity(m) for m in chosen))
        body = [line for m in sorted(chosen, key=entity) for line in links(m)]
        parts.append(f"## {number}. {title}\n\nTables: {names}.\n\n```mermaid\nerDiagram\n" + "\n".join(body)
                     + "\n```\n")
    ours = {c.label for c in apps.get_app_configs() if "site-packages" not in c.path}
    missing = sorted(m._meta.label for m in apps.get_models() if m not in placed and m._meta.app_label in ours)
    if missing:
        raise CommandError(f"Add these apps to a section in core/management/commands/erd.py: {', '.join(missing)}")
    return "\n".join(parts)


class Command(BaseCommand):
    help = "Write config/docs/erd.md from the models (D-103); --check only reports whether it is current."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", help="Fail if erd.md is not what the models give.")

    def handle(self, *args, **options):
        text = render()
        current = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
        if options["check"]:
            if current.replace("\r\n", "\n") != text:
                diff = "".join(list(difflib.unified_diff(current.replace("\r\n", "\n").splitlines(True),
                                                         text.splitlines(True), "erd.md", "models"))[:40])
                raise CommandError(f"erd.md is out of date: run `python manage.py erd`.\n{diff}")
            self.stdout.write("erd.md is current.")
            return
        DOC.write_text(text, encoding="utf-8", newline="\n")
        self.stdout.write(f"Wrote {DOC.relative_to(settings.BASE_DIR)}.")
