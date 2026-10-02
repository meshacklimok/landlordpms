"""The plans in D-060 item 1. Prices are before VAT and can be changed in admin."""

from decimal import Decimal

from django.db import migrations

PLANS = [
    # key, name, units, seats, monthly, yearly, self_serve
    ("free", "Free", 5, 2, "0", "0", True),
    ("starter", "Starter", 25, 4, "799", "7990", True),
    ("business", "Business", 100, 10, "1999", "19990", True),
    ("professional", "Professional", 300, 30, "4999", "49990", True),
    ("enterprise", "Enterprise", None, None, "0", "0", False),
]


def seed(apps, schema_editor):
    Plan = apps.get_model("subscriptions", "Plan")
    for order, (key, name, units, seats, monthly, yearly, self_serve) in enumerate(PLANS):
        Plan.objects.update_or_create(key=key, defaults={
            "name": name, "unit_limit": units, "seat_limit": seats, "monthly_price": Decimal(monthly),
            "yearly_price": Decimal(yearly), "self_serve": self_serve, "sort_order": order})


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0001_platform_billing")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
