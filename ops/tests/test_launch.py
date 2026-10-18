"""Ready for the server: the cron file, the restore test and the launch check (D-098)."""

import glob
import shutil
import subprocess
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command

from core.jobs import JOBS
from core.models import JobRun
from ops import launch, restore

DEPLOY = Path(settings.BASE_DIR) / "deploy"


def _tool(name):
    found = sorted(glob.glob(f"C:/Program Files/PostgreSQL/*/bin/{name}.exe"))
    return found[-1] if found else shutil.which(name)


def test_the_cron_file_runs_every_job_and_nothing_else():
    commands = {}
    for line in (DEPLOY / "crontab").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" not in line.split()[0]:
            fields = line.split()
            assert fields[5:7] == ["sh", "/srv/landlordpms/deploy/run.sh"], line
            commands[fields[7]] = fields[:5]
    assert set(commands) == set(JOBS) | {"check_jobs"}
    assert commands["restore_test"] == ["0", "4", "1", "*", "*"]


def test_the_cron_files_keep_unix_line_endings():
    for name in ("crontab", "run.sh", "setup.sh", "update.sh", "offsite.sh", "monthly_check.sh",
                 "landlordpms.service", "nginx.conf"):
        assert b"\r\n" not in (DEPLOY / name).read_bytes(), name
    attributes = (Path(settings.BASE_DIR) / ".gitattributes").read_text()
    assert "*.sh text eol=lf" in attributes and "deploy/* text eol=lf" in attributes


def test_the_server_files_agree_on_the_app_folder_and_web_server():
    """setup.sh installs the web service and the nginx site (D-122); gunicorn must be in requirements."""
    service = (DEPLOY / "landlordpms.service").read_text(encoding="utf-8")
    nginx = (DEPLOY / "nginx.conf").read_text(encoding="utf-8")
    assert "/srv/landlordpms/venv/bin/gunicorn config.wsgi:application" in service
    assert "--bind 127.0.0.1:8000" in service and "EnvironmentFile=/etc/landlordpms.env" in service
    assert "proxy_pass http://127.0.0.1:8000;" in nginx
    assert "location /media/" not in nginx  # uploads go through the app's permission checks
    requirements = (Path(settings.BASE_DIR) / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("gunicorn==") for line in requirements)


def test_the_scratch_database_is_never_the_live_one():
    assert restore.scratch_name() != settings.DATABASES["default"]["NAME"]
    assert restore.scratch_name().endswith("_restore_test")


def test_no_backup_is_a_failure(settings, tmp_path):
    settings.BACKUP_DIR = str(tmp_path)
    with pytest.raises(restore.RestoreError, match="No backup"):
        restore.restore_test()
    with pytest.raises(restore.RestoreError, match="does not exist"):
        restore.restore_test(tmp_path / "db-20260101-000000.dump")


def test_the_scratch_database_is_dropped_plainly_first_and_forced_only_if_in_use():
    """A forced drop cannot end autovacuum for a role that is not a superuser; a plain drop waits for it."""
    import psycopg

    class Admin:
        def __init__(self, busy):
            self.busy, self.sent = busy, []

        def execute(self, statement):
            text = statement.as_string(None)
            self.sent.append(text)
            if self.busy and "FORCE" not in text:
                raise psycopg.errors.ObjectInUse("in use")

    quiet, busy = Admin(False), Admin(True)
    restore._drop(quiet, "x_restore_test")
    restore._drop(busy, "x_restore_test")
    assert ["FORCE" in text for text in quiet.sent] == [False]
    assert ["FORCE" in text for text in busy.sent] == [False, True]


@pytest.mark.django_db(transaction=True)
def test_a_backup_restores_and_the_scratch_database_is_dropped(settings, tmp_path):
    from accounts.tests.factories import make_org, make_property

    settings.PG_DUMP, settings.PG_RESTORE = _tool("pg_dump"), _tool("pg_restore")
    if not (settings.PG_DUMP and settings.PG_RESTORE):
        pytest.skip("pg_dump and pg_restore are not installed")
    settings.BACKUP_DIR = str(tmp_path)
    settings.MEDIA_ROOT = str(tmp_path / "media")
    make_property(make_org("Restore Homes").organization, "Mvule Court")
    call_command("backup")
    result = restore.restore_test()
    assert result["counts"]["Organization"][0] == result["counts"]["Organization"][1] >= 1
    assert result["migrations_behind"] == [] and result["media_files"] == 0 and result["kept"] == ""
    with restore._connect("postgres") as admin:
        left = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", [restore.scratch_name()]).fetchone()
    assert left is None
    assert "restored in" in restore.summary(result)
    call_command("restore_test")
    assert JobRun.objects.filter(name="restore_test", ok=True).exists()


@pytest.mark.django_db
def test_the_launch_check_fails_on_a_bare_machine(settings, tmp_path):
    settings.BACKUP_DIR = str(tmp_path)
    settings.DEBUG = True
    lines = [text for done, text in launch.checks() if not done]
    assert "DEBUG is on." in lines
    assert any("No backup" in text for text in lines)
    assert any("has never run" in text for text in lines)
    assert any("LocMemCache" in text for text in lines)
    assert any("not a mail server" in text for text in lines)
    with pytest.raises(CommandError, match="still to do"):
        call_command("launch_check")


def test_a_tool_that_will_not_run_has_no_version():
    assert launch.tool_major("no-such-tool-anywhere") is None


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_backups_in_the_project_folder_are_never_committed():
    """Backups default to <project>/backups and data export ZIPs to <project>/exports; both hold tenants' data."""
    paths = ["backups/db-20261001-020000.dump", "backups/media-20261001-020000.tar.gz", "elsewhere.dump",
             "exports/0d5e2c4a-1111-4222-8333-944455556666.zip"]
    result = subprocess.run(["git", "check-ignore", *paths], cwd=settings.BASE_DIR, capture_output=True, text=True)
    assert result.stdout.split() == paths


@pytest.mark.django_db
def test_the_launch_check_asks_for_live_sms_and_m_pesa(settings, tmp_path):
    from django.utils import timezone

    from accounts.tests.factories import make_org
    from mpesa import services
    from mpesa.models import DarajaCredentials

    settings.MEDIA_ROOT = tmp_path
    settings.SMS_BACKEND, settings.AT_USERNAME, settings.OPENAI_API_KEY = "notifications.providers.sms.ConsoleSmsSender", "", ""
    lines = [text for done, text in launch.checks() if not done]
    assert any("SMS goes to ConsoleSmsSender" in text for text in lines)
    assert any("No landlord has live M-Pesa" in text for text in lines)
    assert not any("OPENAI_API_KEY" in text for text in lines)  # optional

    settings.SMS_BACKEND, settings.AT_USERNAME = "notifications.providers.sms.AfricasTalkingSmsSender", "sandbox"
    assert any("AT_USERNAME is sandbox" in text for done, text in launch.checks() if not done)

    owner = make_org(name="Acme")
    account = services.add_account(owner, type="PAYBILL", number="600123", display_name="Rent Paybill")
    services.save_credentials(owner, account, environment="SANDBOX", consumer_key="k", consumer_secret="s")
    DarajaCredentials.objects.update(environment="PRODUCTION", urls_registered_at=None)
    assert any("not accepted the callback URLs" in text for done, text in launch.checks() if not done)

    settings.AT_USERNAME = "nyumbapms"
    DarajaCredentials.objects.update(urls_registered_at=timezone.now())
    done_lines = [text for done, text in launch.checks() if done]
    assert any(text.startswith("SMS go out through Africa's Talking as nyumbapms") for text in done_lines)
    assert any(text.startswith("Live M-Pesa: 1 Paybill") for text in done_lines)


def test_the_server_settings_file_has_every_setting_production_requires():
    """setup.sh writes /etc/landlordpms.env; prod.py refuses to start without these (D-122)."""
    script = (DEPLOY / "setup.sh").read_text(encoding="utf-8")
    written = set()
    inside = False
    for line in script.splitlines():
        if line.startswith('    cat > "$ENV_FILE" <<EOF'):
            inside = True
        elif inside and line == "EOF":
            break
        elif inside and "=" in line and not line.startswith("#"):
            written.add(line.split("=", 1)[0])
    written |= {"VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY"} if "$vapid" in script else set()
    prod = (Path(settings.BASE_DIR) / "config" / "settings" / "prod.py").read_text(encoding="utf-8")
    import re
    required = set(re.findall(r'_required\("([A-Z_]+)"\)', prod)) - {"WA_PHONE_NUMBER_ID", "WA_ACCESS_TOKEN",
                                                                    "WA_APP_SECRET"} | {"SITE_URL"}
    assert required <= written, required - written
    for optional in ("OPENAI_API_KEY", "USSD_CODE", "LEGAL_NAME", "WHATSAPP_BACKEND", "VAPID_SUBJECT"):
        assert optional in written
