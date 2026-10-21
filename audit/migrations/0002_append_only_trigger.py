"""The database itself refuses to edit or delete audit events (D-105, doc 20 §20). See billing 0008."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0001_initial"),
        ("billing", "0008_append_only_triggers"),  # creates landlordpms_append_only()
    ]

    operations = [
        migrations.RunSQL(
            "CREATE TRIGGER audit_auditevent_append_only BEFORE UPDATE OR DELETE ON audit_auditevent "
            "FOR EACH ROW EXECUTE FUNCTION landlordpms_append_only();",
            "DROP TRIGGER IF EXISTS audit_auditevent_append_only ON audit_auditevent;"),
    ]
