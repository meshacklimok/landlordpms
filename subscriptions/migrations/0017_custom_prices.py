from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("subscriptions", "0016_sms_welcome_when"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscription",
            name="custom_monthly_price",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="subscription",
            name="custom_yearly_price",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
    ]
