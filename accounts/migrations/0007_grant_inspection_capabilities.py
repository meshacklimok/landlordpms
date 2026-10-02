"""Gives existing roles and templates the condition report capabilities (D-047).

The catalog sync seeds templates only once and never edits roles, so roles made earlier lack them.
The capabilities are new, so they are created here first when the database already has a catalog.
"""

from django.db import migrations

CAPS = {
    "inspections.view": ("See condition reports, unit item registers and their photos", True),
    "inspections.record": ("Record move-in and move-out condition reports and edit item registers", False),
}

GRANTS = {
    "owner": ("inspections.view", "inspections.record"),
    "manager": ("inspections.view", "inspections.record"),
    "caretaker": ("inspections.view", "inspections.record"),
    "leasing_agent": ("inspections.view", "inspections.record"),
    "maintenance_manager": ("inspections.view",),
    "viewer": ("inspections.view",),
}


def grant(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    RoleTemplate = apps.get_model("accounts", "RoleTemplate")
    Role = apps.get_model("accounts", "Role")
    RoleCapability = apps.get_model("accounts", "RoleCapability")
    if not Capability.objects.exists():
        return  # a fresh database: the catalog sync seeds everything after migrating
    for codename, (description, read_only_safe) in CAPS.items():
        Capability.objects.get_or_create(codename=codename, defaults={
            "module": "inspections", "description": description, "read_only_safe": read_only_safe})
    for key, codenames in GRANTS.items():
        caps = list(Capability.objects.filter(codename__in=codenames))
        for template in RoleTemplate.objects.filter(key=key):
            template.capabilities.add(*caps)
        roles = Role.objects.filter(based_on_template__key=key)
        if key == "owner":
            roles = Role.objects.filter(is_owner_role=True)
        RoleCapability.objects.bulk_create(
            [RoleCapability(role=role, capability=cap) for role in roles for cap in caps], ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_organization_quiet_hours"),
    ]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
