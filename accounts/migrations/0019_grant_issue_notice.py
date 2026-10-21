"""The capability to issue notices to vacate and demand letters (D-109).

As in 0008, the catalog sync never edits roles made earlier, so the capability is created and
granted here when the database already has a catalog.
"""

from django.db import migrations

CODENAME = "leases.issue_notice"
DESCRIPTION = "Issue notices to vacate and demand letters, and record how they were served"
GRANTS = ("owner", "manager")


def grant(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    RoleTemplate = apps.get_model("accounts", "RoleTemplate")
    Role = apps.get_model("accounts", "Role")
    RoleCapability = apps.get_model("accounts", "RoleCapability")
    if not Capability.objects.exists():
        return  # a fresh database: the catalog sync seeds everything after migrating
    cap, _ = Capability.objects.get_or_create(codename=CODENAME, defaults={
        "module": "leases", "description": DESCRIPTION, "read_only_safe": False})
    for key in GRANTS:
        for template in RoleTemplate.objects.filter(key=key):
            template.capabilities.add(cap)
        roles = Role.objects.filter(is_owner_role=True) if key == "owner" else Role.objects.filter(
            based_on_template__key=key)
        RoleCapability.objects.bulk_create([RoleCapability(role=role, capability=cap) for role in roles],
                                           ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0018_organization_require_mfa"),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
