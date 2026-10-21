"""The capability to send documents for e-signature and sign for the landlord (D-118).

As in 0019, the catalog sync never edits roles made earlier, so the capability is created and
granted here when the database already has a catalog.
"""

from django.db import migrations

CODENAME = "leases.esign"
DESCRIPTION = "Send the agreement and move-out statement for e-signature, and sign for the landlord"
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
        ("accounts", "0019_grant_issue_notice"),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
