"""A tag on a plan's card, editable in admin (D-132)."""

from django.db import migrations, models

TAGS = {"lite": "Just starting", "starter": "Most popular", "growth": "Best for growing", "business": "Best value"}


def tag(apps, schema_editor):
    Plan = apps.get_model("subscriptions", "Plan")
    for key, text in TAGS.items():
        Plan.objects.filter(key=key, badge="").update(badge=text)


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0005_professional_by_message")]

    operations = [
        migrations.AddField("plan", "badge", models.CharField(blank=True, max_length=30)),
        migrations.RunPython(tag, migrations.RunPython.noop),
    ]
