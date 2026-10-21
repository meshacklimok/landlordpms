from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0026_holds_deposits")]

    operations = [
        migrations.AlterField(
            model_name="otpcode",
            name="purpose",
            field=models.CharField(
                choices=[
                    ("VERIFY_PHONE", "Verify phone"),
                    ("RESET_PASSWORD", "Reset password"),
                    ("VERIFY_EMAIL", "Verify email"),
                ],
                max_length=20,
            ),
        ),
    ]
