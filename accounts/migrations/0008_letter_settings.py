"""Tenancy letter settings and the capability to issue letters (D-048).

As in 0007, the catalog sync never edits roles made earlier, so the capability is created and
granted here when the database already has a catalog.
"""

from django.db import migrations, models

CODENAME = "tenants.issue_letter"
DESCRIPTION = "Issue tenancy and payment record letters"
GRANTS = ("owner", "manager")


def grant(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    RoleTemplate = apps.get_model("accounts", "RoleTemplate")
    Role = apps.get_model("accounts", "Role")
    RoleCapability = apps.get_model("accounts", "RoleCapability")
    if not Capability.objects.exists():
        return  # a fresh database: the catalog sync seeds everything after migrating
    cap, _ = Capability.objects.get_or_create(codename=CODENAME, defaults={
        "module": "tenants", "description": DESCRIPTION, "read_only_safe": False})
    for key in GRANTS:
        for template in RoleTemplate.objects.filter(key=key):
            template.capabilities.add(cap)
        roles = Role.objects.filter(is_owner_role=True) if key == "owner" else Role.objects.filter(
            based_on_template__key=key)
        RoleCapability.objects.bulk_create([RoleCapability(role=role, capability=cap) for role in roles],
                                           ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_grant_inspection_capabilities"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="letter_show_balance",
            field=models.BooleanField(default=True, verbose_name="balance owed"),
        ),
        migrations.AddField(
            model_name="organization",
            name="letter_show_deposit",
            field=models.BooleanField(default=True, verbose_name="deposit status"),
        ),
        migrations.AddField(
            model_name="organization",
            name="letter_show_payment_record",
            field=models.BooleanField(default=True, verbose_name="on-time payment record"),
        ),
        migrations.AddField(
            model_name="organization",
            name="letter_show_rent",
            field=models.BooleanField(default=True, verbose_name="monthly rent"),
        ),
        migrations.RunPython(grant, migrations.RunPython.noop),
    ]
