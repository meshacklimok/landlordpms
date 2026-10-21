"""The database itself refuses to edit or delete ledger, deposit and follow-up rows (D-105, doc 20 §14).

The models already refuse in save() and delete(); this also stops QuerySet.update(), raw SQL and admin bulk actions.
The trigger is given the columns that may still change (MUTABLE_FIELDS). TRUNCATE is not affected.
"""

from django.db import migrations

FUNCTION = """
CREATE OR REPLACE FUNCTION landlordpms_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    mutable text[] := coalesce(TG_ARGV, '{}'::text[]);
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION '% rows are never deleted', TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
    END IF;
    IF (to_jsonb(NEW) - mutable) IS DISTINCT FROM (to_jsonb(OLD) - mutable) THEN
        RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END $$;
"""

# table -> columns that may change after the row is written
TABLES = {
    "billing_ledgerentry": [],
    "billing_depositentry": ["counterpart_id"],
    "billing_followup": [],
}


def _create(table, mutable):
    args = ", ".join(f"'{column}'" for column in mutable)
    return (f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION landlordpms_append_only({args});")


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0007_electricity_meters"),
    ]

    operations = [
        migrations.RunSQL(FUNCTION, migrations.RunSQL.noop),
        *[migrations.RunSQL(_create(table, mutable), f"DROP TRIGGER IF EXISTS {table}_append_only ON {table};")
          for table, mutable in TABLES.items()],
    ]
