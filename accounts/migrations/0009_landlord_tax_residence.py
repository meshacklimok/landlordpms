"""The landlord's tax residence, for the rental income tax estimate (D-050)."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_letter_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="landlord_tax_residence",
            field=models.CharField(
                choices=[("RESIDENT", "Resident in Kenya"), ("NON_RESIDENT", "Non-resident")],
                default="RESIDENT", max_length=12, verbose_name="landlord tax residence"),
        ),
    ]
