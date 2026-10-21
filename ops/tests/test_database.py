"""The database layer: connection options, append-only triggers, monitoring and housekeeping (D-105, doc 20)."""

import datetime
from io import StringIO

import pytest
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from accounts.tests.factories import make_org
from audit.models import AuditEvent
from billing.models import DepositEntry, LedgerEntry
from config.settings.base import database_options
from ops import alerts, dbhealth, launch, scale

pytestmark = pytest.mark.django_db


# --- connection options -------------------------------------------------------------------------------------------

def test_options_off_by_default():
    options = database_options(pool=False, statement_timeout_ms=0, idle_in_transaction_ms=0)
    assert "pool" not in options and "options" not in options
    assert options["application_name"] and options["sslmode"]


def test_options_with_a_pool_and_timeouts():
    options = database_options(pool=True, statement_timeout_ms=30000, idle_in_transaction_ms=60000)
    assert options["pool"]["max_size"] >= options["pool"]["min_size"] >= 1
    assert options["options"] == "-c statement_timeout=30000 -c idle_in_transaction_session_timeout=60000"


# --- append-only, enforced by the database ------------------------------------------------------------------------

@pytest.fixture
def ledger_entry():
    owner = make_org()
    prop = scale.make_property(owner)
    with scale.quiet():
        scale.add_lease(owner, prop, 1)
    return LedgerEntry.objects.filter(organization=owner.organization).first()


def _refused(sql, params):
    with pytest.raises(IntegrityError, match="append-only|never deleted"), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(sql, params)


def test_a_bulk_update_of_the_ledger_is_refused(ledger_entry):
    with pytest.raises(IntegrityError, match="append-only"), transaction.atomic():
        LedgerEntry.objects.filter(pk=ledger_entry.pk).update(reason="edited")


def test_raw_sql_cannot_edit_or_delete_the_ledger(ledger_entry):
    _refused("UPDATE billing_ledgerentry SET amount = 1 WHERE id = %s", [ledger_entry.pk])
    _refused("DELETE FROM billing_ledgerentry WHERE id = %s", [ledger_entry.pk])
    assert LedgerEntry.objects.get(pk=ledger_entry.pk).amount == ledger_entry.amount


def test_audit_events_cannot_be_edited_or_deleted():
    event = AuditEvent.objects.create(action="test.event")
    _refused("UPDATE audit_auditevent SET action = 'x' WHERE id = %s", [event.pk])
    _refused("DELETE FROM audit_auditevent WHERE id = %s", [event.pk])


def test_the_deposit_counterpart_may_still_be_set():
    """DepositEntry.MUTABLE_FIELDS: the trigger allows that column and nothing else."""
    with connection.cursor() as cursor:
        cursor.execute("""SELECT tgargs FROM pg_trigger WHERE tgname = 'billing_depositentry_append_only'""")
        assert b"counterpart_id" in bytes(cursor.fetchone()[0])
    assert DepositEntry.MUTABLE_FIELDS == ("counterpart",)


# --- monitoring ---------------------------------------------------------------------------------------------------

def test_the_report_reads_the_live_database():
    r = dbhealth.report()
    assert r.connections["max"] > 0 and r.storage["database_bytes"] > 0
    assert any(t["table"] == "billing_ledgerentry" for t in dbhealth.storage_stats(top=500)["tables"])
    assert r.unapplied == [] and r.settings["superuser"] in (True, False)


def test_a_healthy_database_has_no_problems():
    assert dbhealth.problems() == []


def test_thresholds_raise_problems(settings):
    settings.DB_ALERT_CONNECTIONS_PERCENT = 0
    settings.DB_ALERT_DISK_FREE_PERCENT = 101
    settings.DB_ALERT_SIZE_MB = 1
    lines = dbhealth.problems()
    assert any(line.startswith("database connections at") for line in lines)
    assert any(line.startswith("disk for") for line in lines)
    assert any("DB_ALERT_SIZE_MB" in line for line in lines)
    # The size and disk lines carry live figures (MB, GB, % free) that can move between the two calls: compare
    # them by their start.
    live = ("database is ", "disk for ")
    stable = {line for line in lines if not line.startswith(live)}
    assert stable <= set(alerts.current_problems())
    assert any(line.startswith("database is ") for line in alerts.current_problems())
    assert any(line.startswith("disk for ") for line in alerts.current_problems())


def test_long_running_never_shows_query_text():
    """pg_stat_activity holds bound values such as phone numbers; only who and how long leave it."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    for row in dbhealth.long_running(seconds=0):
        assert set(row) == {"pid", "app", "state", "seconds"}


def test_db_health_command(settings):
    out = StringIO()
    call_command("db_health", stdout=out)
    assert "Connections:" in out.getvalue() and "No database problems." in out.getvalue()


def test_the_launch_check_covers_the_database():
    lines = [text for _done, text in launch.checks()]
    assert any("superuser" in t for t in lines)
    assert any("pooled" in t for t in lines)
    assert any("pg_stat_statements" in t for t in lines)
    assert "Every migration is applied." in lines


# --- housekeeping -------------------------------------------------------------------------------------------------

def test_expired_sessions_are_purged():
    now = timezone.now()
    Session.objects.create(session_key="old", session_data="", expire_date=now - datetime.timedelta(days=1))
    Session.objects.create(session_key="live", session_data="", expire_date=now + datetime.timedelta(days=1))
    call_command("purge_sessions", stdout=StringIO())
    assert list(Session.objects.values_list("session_key", flat=True)) == ["live"]


def test_old_messages_lose_their_text_but_keep_the_row(settings):
    from notifications import catalog
    from notifications.models import Message
    from notifications.services import clear_old_text

    owner = make_org()
    settings.MESSAGE_TEXT_DAYS = 30

    def message(days_ago, status=Message.Status.SENT):
        m = Message.objects.create(organization=owner.organization, type="announcement", user=owner.user,
                                   channel=catalog.SMS, to="+254712000001", body="Rent of KES 20,000 is due",
                                   status=status, dedupe_key=f"k{days_ago}{status}")
        Message.objects.filter(pk=m.pk).update(created_at=timezone.now() - datetime.timedelta(days=days_ago))
        return m

    old, recent, waiting = message(31), message(29), message(31, Message.Status.QUEUED)
    assert clear_old_text(batch=1) == 1
    old.refresh_from_db()
    assert (old.body, old.to, old.status, old.dedupe_key) == ("", "", Message.Status.SENT, "k31SENT")
    assert Message.objects.get(pk=recent.pk).body and Message.objects.get(pk=waiting.pk).body
    assert clear_old_text() == 0
    call_command("purge_messages", stdout=StringIO())


def test_row_locks_with_joins_lock_only_their_own_row():
    """`select_for_update()` with `select_related` locks the joined rows too. Those are shared (the organization,
    a property, a payment account), and every insert pointing at them holds a share lock, so two M-Pesa payments
    at once deadlocked in the load test (doc 20 §30). Such locks must say `of=("self",)`."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    found = []
    for path in root.rglob("*.py"):
        parts = set(path.relative_to(root).parts)
        if parts & {"venv", "migrations", "tests", "node_modules"}:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in {"select_related", "select_for_update"}:
                chain = ast.unparse(node)
                if "select_for_update()" in chain and "select_related(" in chain:
                    found.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not found, found


def test_loadtest_setup_writes_working_sessions_and_a_callback(tmp_path, client, settings):
    """What `tests/loadtest/locustfile.py` uses must work: each session opens its pages and the callback is accepted."""
    import json

    from django.test import Client

    from mpesa.models import MpesaTransaction

    out = tmp_path / "setup.json"
    call_command("loadtest_setup", "--leases", "3", "--staff", "1", "--tenants", "2", "--out", str(out), "--confirm",
                 stdout=StringIO())
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["tenants"]) == 2 and len(data["references"]) == len(data["lease_pages"]) == 3

    staff = Client()
    staff.cookies[data["session_cookie"]] = data["staff_sessions"][0]
    for path in ["/", data["lease_pages"][0], *data["staff_pages"]]:
        assert staff.get(path).status_code == 200, path
    tenant = Client()
    tenant.cookies[data["session_cookie"]] = data["tenants"][0]["session"]
    assert tenant.get("/my/").status_code == 200
    assert tenant.get(f"/my/leases/{data['tenants'][0]['lease']}/").status_code == 200

    payload = {"TransactionType": "Pay Bill", "TransID": "LTTEST0001", "TransTime": "20260203101500",
               "TransAmount": "500.00", "BusinessShortCode": data["shortcode"],
               "BillRefNumber": data["references"][0], "MSISDN": "254700000000", "FirstName": "Load"}
    response = client.post(data["callback"], payload, content_type="application/json")
    assert response.json()["ResultCode"] == 0
    assert MpesaTransaction.objects.get(trans_id="LTTEST0001").status == MpesaTransaction.Status.MATCHED


def test_loadtest_setup_refuses_real_senders(tmp_path, settings):
    from django.core.management import CommandError

    settings.SMS_BACKEND = "notifications.providers.sms.AfricasTalkingSmsSender"
    with pytest.raises(CommandError, match="real phones"):
        call_command("loadtest_setup", "--leases", "1", "--out", str(tmp_path / "s.json"), stdout=StringIO())
