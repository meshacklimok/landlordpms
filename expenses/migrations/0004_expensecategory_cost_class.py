from django.db import migrations, models

# Existing categories are sorted by what their name says, so landlords see sensible groups straight away (D-191).
KEYWORDS = (
    ("MANAGEMENT", ("management fee", "agent fee", "agency fee", "commission")),
    ("SALARIES", ("wage", "salar", "caretaker", "payroll", "staff")),
    ("SECURITY", ("security", "guard", "alarm", "cctv")),
    ("GARBAGE", ("garbage", "refuse", "waste", "clean")),
    ("INSURANCE", ("insur",)),
    ("UTILITIES", ("water", "electric", "power", "kplc")),
    ("MAINTENANCE", ("repair", "maintenance", "plumb", "paint", "fix")),
)


def sort_existing(apps, schema_editor):
    Category = apps.get_model("expenses", "ExpenseCategory")
    for category in Category.objects.all():
        name = category.name.lower()
        for cost_class, words in KEYWORDS:
            if any(w in name for w in words):
                category.cost_class = cost_class
                category.save(update_fields=["cost_class"])
                break


class Migration(migrations.Migration):

    dependencies = [
        ("expenses", "0003_expense_is_recurring"),
    ]

    operations = [
        migrations.AddField(
            model_name="expensecategory",
            name="cost_class",
            field=models.CharField(
                choices=[
                    ("MAINTENANCE", "Maintenance and repairs"),
                    ("SALARIES", "Caretaker and staff salaries"),
                    ("SECURITY", "Security"),
                    ("GARBAGE", "Garbage and cleaning"),
                    ("INSURANCE", "Insurance"),
                    ("UTILITIES", "Water and electricity bills"),
                    ("MANAGEMENT", "Property management fees"),
                    ("OTHER", "Other operating costs"),
                ],
                default="OTHER",
                max_length=12,
                verbose_name="kind of cost",
            ),
        ),
        migrations.RunPython(sort_existing, migrations.RunPython.noop),
    ]
