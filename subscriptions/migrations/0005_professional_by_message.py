"""Above 100 units the price is agreed by message, so Professional is no longer self-serve (D-132).
For databases that already ran 0004; its price stays as a guide for the Platform Admin."""

from django.db import migrations


def apply(apps, schema_editor):
    apps.get_model("subscriptions", "Plan").objects.filter(key="professional").update(self_serve=False)


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0004_unit_bands_and_one_off")]

    operations = [migrations.RunPython(apply, migrations.RunPython.noop)]
