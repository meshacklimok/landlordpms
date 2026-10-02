"""Gives existing roles and templates the deposit capabilities added in Phase 3.

The catalog sync seeds templates only once and never edits roles, so roles made before Phase 3 lack them.
Owners get both, as do Managers; Accountants record but do not deduct (doc 13).
"""

from django.db import migrations

GRANTS = {
    "owner": ("deposits.record", "deposits.deduct"),
    "manager": ("deposits.record", "deposits.deduct"),
    "accountant": ("deposits.record",),
}


def grant(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    RoleTemplate = apps.get_model("accounts", "RoleTemplate")
    Role = apps.get_model("accounts", "Role")
    RoleCapability = apps.get_model("accounts", "RoleCapability")
    for key, codenames in GRANTS.items():
        caps = list(Capability.objects.filter(codename__in=codenames))
        if not caps:
            continue  # a fresh database: the catalog sync seeds them after migrating
        for template in RoleTemplate.objects.filter(key=key):
            template.capabilities.add(*caps)
        roles = Role.objects.filter(based_on_template__key=key)
        if key == "owner":
            roles = Role.objects.filter(is_owner_role=True)
        RoleCapability.objects.bulk_create(
            [RoleCapability(role=role, capability=cap) for role in roles for cap in caps], ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_organization_billing_settings"),
    ]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
