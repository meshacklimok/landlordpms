"""Unit bands with every feature included, a one-off price for small landlords, and Free off by default (D-132).

Prices are before VAT and can all be changed in Django admin."""

from decimal import Decimal

from django.db import migrations, models

# Above 100 units the price is agreed by message: Professional is not self-serve (its price is a guide).
# key, name, units, seats, monthly, yearly, one-off. Yearly is ten months (two free); one-off is two and a
# half years of yearly for five years of use. Only the bands under 20 units have a one-off price.
BANDS = [
    ("lite", "Lite", 10, 3, "499", "4990", "12490"),
    ("starter", "Starter", 20, 4, "799", "7990", "19990"),
    ("growth", "Growth", 50, 6, "1299", "12990", "0"),
    ("business", "Business", 100, 10, "1999", "19990", "0"),
    ("professional", "Professional", 300, 30, "4999", "49990", "0"),
]
ORDER = ["free", "lite", "starter", "growth", "business", "professional", "enterprise"]


def seed(apps, schema_editor):
    Plan = apps.get_model("subscriptions", "Plan")
    for key, name, units, seats, monthly, yearly, once in BANDS:
        Plan.objects.update_or_create(key=key, defaults={
            "name": name, "unit_limit": units, "seat_limit": seats, "monthly_price": Decimal(monthly),
            "yearly_price": Decimal(yearly), "one_off_price": Decimal(once), "one_off_years": 5,
            "self_serve": key != "professional", "is_active": True})
    # Free stays as a row so a Platform Admin can switch it on, but nobody is offered it until then.
    Plan.objects.filter(key="free").update(is_active=False)
    for position, key in enumerate(ORDER):
        Plan.objects.filter(key=key).update(sort_order=position)


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0003_platform_payments")]

    operations = [
        migrations.AddField("plan", "one_off_price",
                            models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=12)),
        migrations.AddField("plan", "one_off_years", models.PositiveSmallIntegerField(default=5)),
        migrations.AlterField("subscription", "interval", models.CharField(
            choices=[("MONTHLY", "Monthly"), ("YEARLY", "Yearly"), ("ONCE", "One-off")], default="MONTHLY",
            max_length=10)),
        migrations.AlterField("subscriptioninvoice", "interval", models.CharField(
            choices=[("MONTHLY", "Monthly"), ("YEARLY", "Yearly"), ("ONCE", "One-off")], max_length=10)),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
