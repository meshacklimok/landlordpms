"""The metered water capabilities (D-057).

As in 0010, the catalog sync never edits roles made earlier, so the capabilities are created and
granted here when the database already has a catalog.
"""

from django.db import migrations

CAPS = {
    "meters.view": ("See water meters, readings and their photos", True,
                    ("owner", "manager", "accountant", "caretaker", "viewer")),
    "meters.record": ("Record meter readings", False, ("owner", "manager", "caretaker")),
    "meters.approve": ("Approve or reject meter readings for billing", False, ("owner", "manager", "accountant")),
    "meters.manage": ("Add and edit meters, their units and rates", False, ("owner", "manager")),
}


def grant(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    RoleTemplate = apps.get_model("accounts", "RoleTemplate")
    Role = apps.get_model("accounts", "Role")
    RoleCapability = apps.get_model("accounts", "RoleCapability")
    if not Capability.objects.exists():
        return  # a fresh database: the catalog sync seeds everything after migrating
    for codename, (description, read_only_safe, keys) in CAPS.items():
        cap, _ = Capability.objects.get_or_create(codename=codename, defaults={
            "module": "meters", "description": description, "read_only_safe": read_only_safe})
        for key in keys:
            for template in RoleTemplate.objects.filter(key=key):
                template.capabilities.add(cap)
            roles = Role.objects.filter(is_owner_role=True) if key == "owner" else Role.objects.filter(
                based_on_template__key=key)
            RoleCapability.objects.bulk_create([RoleCapability(role=role, capability=cap) for role in roles],
                                               ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0010_grant_follow_up_capability"),
    ]

    operations = [
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
