from django.db import migrations, models


def percent_to_equal(apps, schema_editor):
    Meter = apps.get_model("meters", "Meter")
    MeterReading = apps.get_model("meters", "MeterReading")
    Meter.objects.filter(split="PERCENT").update(split="EQUAL")
    MeterReading.objects.filter(split="PERCENT").update(split="EQUAL")


class Migration(migrations.Migration):
    dependencies = [
        ("meters", "0010_metercharge_allocation"),
    ]

    operations = [
        migrations.RunPython(
            percent_to_equal,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="meter",
            name="split",
            field=models.CharField(
                choices=[
                    ("EQUAL", "Equally"),
                    ("WEIGHTED", "By weight"),
                    ("USAGE", "By each room's own meter"),
                ],
                default="EQUAL",
                max_length=10,
                verbose_name="shared meter split",
            ),
        ),
        migrations.AlterField(
            model_name="meterreading",
            name="split",
            field=models.CharField(
                blank=True,
                choices=[
                    ("EQUAL", "Equally"),
                    ("WEIGHTED", "By weight"),
                    ("USAGE", "By each room's own meter"),
                ],
                editable=False,
                max_length=10,
            ),
        ),
    ]
