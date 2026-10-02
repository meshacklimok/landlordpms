"""Job records, alerts, health checks and backup pruning (D-061, D-062)."""

import datetime
from io import StringIO
from pathlib import Path

import pytest
from django.core import mail
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from core import alerts, backup, health, jobs
from core.models import JobRun

pytestmark = pytest.mark.django_db


def _all_jobs_ran(at=None):
    at = at or timezone.now()
    for name in jobs.JOBS:
        JobRun.objects.create(name=name, started_at=at, finished_at=at, ok=True)


# --- job records -------------------------------------------------------------

def test_a_job_command_records_its_run():
    call_command("subscriptions_daily", stdout=StringIO())
    run = JobRun.objects.get(name="subscriptions_daily")
    assert run.ok is True and run.finished_at and run.summary


def test_a_failed_job_is_recorded_and_raised(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("database went away")

    monkeypatch.setattr("billing.management.commands.billing_daily.run_daily", boom)
    with pytest.raises(RuntimeError):
        call_command("billing_daily", stdout=StringIO())
    run = JobRun.objects.get(name="billing_daily")
    assert run.ok is False and "database went away" in run.error


@pytest.mark.parametrize("command", ["send_due_messages", "billing_daily", "mpesa_daily", "subscriptions_daily",
                                     "purge_otp_codes", "purge_import_previews"])
def test_every_scheduled_command_is_a_known_job(command):
    call_command(command, stdout=StringIO())
    assert JobRun.objects.filter(name=command, ok=True).exists()
    assert command in jobs.JOBS


def test_late_failed_and_missing_jobs_are_problems():
    now = timezone.now()
    _all_jobs_ran(now)
    assert jobs.problems(now) == []
    assert set(jobs.job_states(now).values()) == {"ok"}
    JobRun.objects.create(name="billing_daily", started_at=now, finished_at=now, ok=False, error="x")
    later = now + datetime.timedelta(hours=1)
    lines = jobs.problems(later)
    assert len(lines) == 2  # billing_daily failed; send_due_messages is 60 minutes old
    assert any(line.startswith("billing_daily: last run failed") for line in lines)
    assert any(line.startswith("send_due_messages: no successful run") for line in lines)
    assert jobs.job_states(later)["billing_daily"] == "failed"


def test_old_runs_are_purged():
    old = timezone.now() - datetime.timedelta(days=91)
    JobRun.objects.create(name="backup", started_at=old, ok=True)
    JobRun.objects.create(name="backup", ok=True)
    assert jobs.purge_old_runs() == 1
    assert JobRun.objects.count() == 1


# --- alerts ------------------------------------------------------------------

def test_alerts_once_then_quiet_then_all_clear(settings):
    settings.OPS_ALERT_EMAILS = "ops@example.com"
    now = timezone.now()
    assert alerts.check_and_alert(now) == "alert"  # nothing has ever run
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["ops@example.com"]
    assert alerts.check_and_alert(now + datetime.timedelta(minutes=15)) == "quiet"
    assert alerts.check_and_alert(now + datetime.timedelta(hours=7)) == "alert"  # still broken: say it again
    assert len(mail.outbox) == 2
    _all_jobs_ran(now + datetime.timedelta(hours=7))
    assert alerts.check_and_alert(now + datetime.timedelta(hours=7, minutes=5)) == "all clear"
    assert "All clear" in mail.outbox[2].subject
    assert alerts.check_and_alert(now + datetime.timedelta(hours=7, minutes=10)) == "ok"
    assert len(mail.outbox) == 3


def test_a_new_problem_alerts_at_once(settings):
    settings.OPS_ALERT_EMAILS = "ops@example.com"
    now = timezone.now()
    _all_jobs_ran(now)
    JobRun.objects.create(name="backup", started_at=now, finished_at=now, ok=False)
    assert alerts.check_and_alert(now) == "alert"
    JobRun.objects.create(name="mpesa_daily", started_at=now, finished_at=now, ok=False)
    assert alerts.check_and_alert(now + datetime.timedelta(minutes=15)) == "alert"
    assert len(mail.outbox) == 2


def test_alerts_fall_back_to_admins(settings):
    settings.OPS_ALERT_EMAILS = ""
    settings.ADMINS = [("Ops", "admin@example.com")]
    assert alerts.recipients() == ["admin@example.com"]


def test_check_jobs_command(settings):
    settings.OPS_ALERT_EMAILS = "ops@example.com"
    out = StringIO()
    call_command("check_jobs", stdout=out)
    assert "alert" in out.getvalue()


# --- health ------------------------------------------------------------------

def test_healthz_ok(client):
    response = client.get(reverse("healthz"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "cache": "ok"}}


def test_healthz_names_the_failing_check_and_nothing_else(client, monkeypatch):
    monkeypatch.setattr(health, "cache_ok", lambda: False)
    response = client.get(reverse("healthz"))
    assert response.status_code == 503
    assert response.json() == {"status": "fail", "checks": {"database": "ok", "cache": "fail"}}


# --- backups -----------------------------------------------------------------

def _stamps(start: datetime.date, days: int) -> list[str]:
    return [f"{start + datetime.timedelta(days=i):%Y%m%d}-021000" for i in range(days)]


def test_keep_policy():
    stamps = _stamps(datetime.date(2025, 1, 1), 500)  # to 15 May 2026, a Friday
    kept = backup.keep(stamps)
    assert set(sorted(stamps)[-7:]) <= kept  # 9 to 15 May
    firsts = {f"2025{m:02d}01-021000" for m in range(6, 13)} | {f"2026{m:02d}01-021000" for m in range(1, 6)}
    assert firsts <= kept  # the 1st of June 2025 to May 2026
    mondays = {"20260420-021000", "20260427-021000", "20260504-021000", "20260511-021000"}
    assert mondays <= kept  # the first day of each of the last 4 weeks
    assert len(kept) == len(set(sorted(stamps)[-7:]) | firsts | mondays)
    assert "20250501-021000" not in kept  # more than 12 months back


def test_keep_takes_each_days_latest():
    kept = backup.keep(["20260901-021000", "20260901-150000"])
    assert "20260901-150000" in kept and "20260901-021000" not in kept


def test_prune_leaves_other_files(tmp_path: Path):
    for stamp in _stamps(datetime.date(2026, 1, 1), 60):
        (tmp_path / f"db-{stamp}.dump").write_text("x")
        (tmp_path / f"media-{stamp}.tar.gz").write_text("x")
    (tmp_path / "notes.txt").write_text("keep me")
    removed = backup.prune(tmp_path)
    assert removed
    assert (tmp_path / "notes.txt").exists()
    left = {p.name for p in tmp_path.iterdir()}
    assert "db-20260301-021000.dump" in left and "media-20260301-021000.tar.gz" in left


def test_backup_command_records_a_failure(settings, tmp_path):
    settings.BACKUP_DIR = str(tmp_path)
    settings.PG_DUMP = str(tmp_path / "no-such-pg_dump")
    with pytest.raises(backup.BackupError):
        call_command("backup", stdout=StringIO())
    run = JobRun.objects.get(name="backup")
    assert run.ok is False and "not installed" in run.error
    assert not any(tmp_path.glob("db-*"))
