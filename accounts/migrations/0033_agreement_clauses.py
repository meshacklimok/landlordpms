from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0032_lapse_notify"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="agreement_clauses",
            field=models.TextField(blank=True, verbose_name="agreement wording"),
        ),
    ]
