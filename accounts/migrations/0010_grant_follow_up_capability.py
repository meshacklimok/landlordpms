"""The capability to record calls and promises to pay (D-052).

As in 0007 and 0008, the catalog sync never edits roles made earlier, so the capability is
created and granted here when the database already has a catalog.
"""

from django.db import migrations

CODENAME = "arrears.follow_up"
DESCRIPTION = "Record calls and promises to pay"
GRANTS = ("owner", "manager", "accountant")


def grant(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    RoleTemplate = apps.get_model("accounts", "RoleTemplate")
    Role = apps.get_model("accounts", "Role")
    RoleCapability = apps.get_model("accounts", "RoleCapability")
    if not Capability.objects.exists():
        return  # a fresh database: the catalog sync seeds everything after migrating
    cap, _ = Capability.objects.get_or_create(codename=CODENAME, defaults={
        "module": "arrears", "description": DESCRIPTION, "read_only_safe": False})
    for key in GRANTS:
        for template in RoleTemplate.objects.filter(key=key):
            template.capabilities.add(cap)
        roles = Role.objects.filter(is_owner_role=True) if key == "owner" else Role.objects.filter(
            based_on_template__key=key)
        RoleCapability.objects.bulk_create([RoleCapability(role=role, capability=cap) for role in roles],
                                           ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_landlord_tax_residence"),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
