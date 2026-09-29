"""Metered water on invoices (D-057): a line may point at the meter charge it bills."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0005_followup"),
        ("meters", "0001_initial"),
    ]

    operations = [
        # Metered water lines (D-057) are exempt: a lease may have several in one month.
        migrations.RemoveConstraint(
            model_name="invoiceline",
            name="billing_invoiceline_billed_once",
        ),
        migrations.AddField(
            model_name="invoiceline",
            name="meter_charge",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lines",
                to="meters.metercharge",
            ),
        ),
        migrations.AlterField(
            model_name="chargetype",
            name="category",
            field=models.CharField(
                choices=[
                    ("RENT", "Rent"),
                    ("DEPOSIT", "Deposit"),
                    ("WATER", "Water"),
                    ("ELECTRICITY", "Electricity"),
                    ("GARBAGE", "Garbage"),
                    ("SERVICE_CHARGE", "Service charge"),
                    ("SECURITY", "Security"),
                    ("PARKING", "Parking"),
                    ("LATE_FEE", "Late fee"),
                    ("METERED_WATER", "Metered water"),
                    ("OTHER", "Other"),
                ],
                default="OTHER",
                max_length=20,
                verbose_name="category",
            ),
        ),
        migrations.AddConstraint(
            model_name="invoiceline",
            constraint=models.UniqueConstraint(
                models.F("lease"),
                models.F("billing_month"),
                models.F("charge_type"),
                condition=models.Q(("is_void", False), ("meter_charge__isnull", True)),
                name="billing_invoiceline_billed_once",
            ),
        ),
        migrations.AddConstraint(
            model_name="invoiceline",
            constraint=models.UniqueConstraint(
                models.F("meter_charge"),
                condition=models.Q(("is_void", False), ("meter_charge__isnull", False)),
                name="billing_invoiceline_meter_charge_billed_once",
            ),
        ),
    ]
