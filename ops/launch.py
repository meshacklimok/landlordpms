"""What the server still lacks before launch (D-098 item 4). Each check is (done, text)."""

import datetime
import re
import shutil
import subprocess
import uuid

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.db import connection
from django.utils import timezone

from core import jobs

from . import alerts, backup, dbhealth

# Things a check on the server cannot see; printed every time.
REMINDERS = [
    "Off-site copy: BACKUP_DIR is synced each night to storage in another data centre that cannot delete old "
    "versions (doc 18, Backups).",
    "Uptime monitor: an outside service opens /healthz every few minutes and alerts on failure (doc 18, Alerts).",
]
FRESH_BACKUP_HOURS = 26


def tool_major(path: str) -> int | None:
    """The major version of a PostgreSQL client tool, from `<tool> --version`, or None if it does not run."""
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=30).stdout  # noqa: S603 - no shell
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\)\s*(\d+)", out) or re.search(r"(\d+)", out)
    return int(match.group(1)) if match else None


def _tools() -> list[tuple[bool, str]]:
    connection.ensure_connection()
    server = connection.pg_version // 10000
    results = []
    for name, setting in (("pg_dump", "PG_DUMP"), ("pg_restore", "PG_RESTORE")):
        path = getattr(settings, setting, "") or shutil.which(name) or name
        major = tool_major(path)
        if major is None:
            results.append((False, f"{name} is not installed or not on PATH (or set {setting})."))
        elif major < server:
            results.append((False, f"{name} is version {major}, older than the database server ({server})."))
        else:
            results.append((True, f"{name} {major} found; the database server is {server}."))
    return results


def _backups(now) -> list[tuple[bool, str]]:
    folder = backup.backup_dir()
    probe = folder / f".launch-check-{uuid.uuid4().hex}"
    try:
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        return [(False, f"The backup folder {folder} cannot be written to: {e}")]
    stamps = [m.group(2) for p in folder.glob("db-*.dump") if (m := backup.NAME.match(p.name))]
    if not stamps:
        return [(False, f"No backup in {folder} yet: run `python manage.py backup`.")]
    newest = timezone.make_aware(datetime.datetime.strptime(max(stamps), "%Y%m%d-%H%M%S"))
    if (now - newest).total_seconds() > FRESH_BACKUP_HOURS * 3600:
        return [(False, f"The newest backup in {folder} is from {newest:%Y-%m-%d %H:%M}, over a day old.")]
    return [(True, f"Backups are written to {folder}; the newest is from {newest:%Y-%m-%d %H:%M}.")]


def _cache() -> tuple[bool, str]:
    backend = settings.CACHES["default"]["BACKEND"]
    if "locmem" in backend.lower() or "dummy" in backend.lower():
        return False, f"The cache is {backend.rsplit('.', 1)[-1]}: rate limits need one shared cache (Redis)."
    key = f"launch-check-{uuid.uuid4().hex}"
    try:
        cache.set(key, "ok", 30)
        answered = cache.get(key) == "ok"
        cache.delete(key)
    except Exception as e:  # noqa: BLE001 - any cache failure is the finding
        return False, f"The cache does not answer: {e}"
    return (True, "The cache answers.") if answered else (False, "The cache did not keep a value.")


def _mail_login(alias: str) -> tuple[bool, str]:
    """Connects and signs in to the mail server without sending anything, so a wrong password shows here."""
    try:
        connection = mail.mailers[alias]
        connection.open()
        connection.close()
    except Exception as e:  # noqa: BLE001 - any failure to connect or sign in is the finding
        return False, f"Email ({alias}) could not sign in to its mail server: {e}"
    return True, f"Email ({alias}) signs in to its mail server."


def _email() -> list[tuple[bool, str]]:
    results = []
    for alias in settings.MAILERS:
        backend = settings.MAILERS[alias]["BACKEND"]
        results.append((False, f"Email ({alias}) goes to the {backend.rsplit('.', 2)[-2]} backend, not a mail server.")
                       if any(word in backend for word in ("console", "locmem", "dummy", "filebased"))
                       else _mail_login(alias))
    results.append((True, f"Alerts go to {', '.join(alerts.recipients())}.") if alerts.recipients()
                   else (False, "Nobody gets alerts: set OPS_ALERT_EMAILS or ADMINS."))
    return results


LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}


def _database() -> list[tuple[bool, str]]:
    """How the app connects, and what it may do once connected (doc 20 §1-2, §18-19, §22)."""
    db = settings.DATABASES["default"]
    options, s = db.get("OPTIONS", {}), dbhealth.session_settings()
    host = db.get("HOST") or ""
    local = host in LOCAL_HOSTS or host.startswith("/")
    results = []
    if s["ssl_in_use"]:
        results.append((True, "The database connection is encrypted (TLS)."))
    elif local:
        results.append((True, "The database is on this server, so its connection does not need TLS."))
    else:
        results.append((False, f"The database at {host} is reached without TLS: set DB_SSLMODE=require "
                               "(or verify-full with the provider's certificate)."))
    results.append((not s["superuser"], "The app's database user is not a superuser." if not s["superuser"] else
                    f"The app connects as {db.get('USER')}, a superuser: use the roles in deploy/db_roles.sql."))
    results.append((bool(options.get("pool")), "Connections are pooled." if options.get("pool") else
                    "Connections are not pooled: set DB_POOL=1."))
    timed = s.get("statement_timeout") not in (None, "0")
    results.append((timed, f"Statements are stopped after {s.get('statement_timeout')} ms." if timed else
                    "No statement timeout: set DB_STATEMENT_TIMEOUT_MS (production default 30000)."))
    results.append((dbhealth.has_pg_stat_statements(),
                    "pg_stat_statements records the slowest queries." if dbhealth.has_pg_stat_statements() else
                    "pg_stat_statements is not installed, so slow queries cannot be found (doc 20, Monitoring)."))
    pending = dbhealth.unapplied_migrations()
    results.append((not pending, "Every migration is applied." if not pending else
                    f"{len(pending)} migration(s) are not applied: run `python manage.py migrate`."))
    return results


def _providers() -> list[tuple[bool, str]]:
    """The outside accounts: live SMS keys, and at least one landlord able to take M-Pesa payments."""
    from mpesa.models import DarajaCredentials

    results = []
    if settings.SMS_BACKEND != "notifications.providers.sms.AfricasTalkingSmsSender":
        results.append((False, f"SMS goes to {settings.SMS_BACKEND.rsplit('.', 1)[-1]}: set SMS_BACKEND to "
                               "notifications.providers.sms.AfricasTalkingSmsSender with AT_USERNAME and AT_API_KEY "
                               "(Django admin > Platform settings, or the environment file)."))
    elif settings.AT_USERNAME == "sandbox":
        results.append((False, "AT_USERNAME is sandbox: put in your live Africa's Talking username and API key."))
    else:
        sender = settings.AT_SENDER_ID or "Africa's Talking's shared sender (AT_SENDER_ID is empty)"
        results.append((True, f"SMS go out through Africa's Talking as {settings.AT_USERNAME}, from {sender}."))
    if settings.SMS_WALLET_ENFORCED:
        results.append((True, "The SMS wallet is enforced: prepaid SMS are charged and stop at zero credit."))
    else:
        results.append((False, "SMS_WALLET_ENFORCED is off: every landlord's SMS go out free at our cost. Remove "
                               "SMS_WALLET_ENFORCED=0 from the server's environment file."))
    live = DarajaCredentials.Environment.PRODUCTION
    daraja =DarajaCredentials.objects.filter(environment=live).exclude(consumer_key_encrypted="")
    registered = daraja.filter(urls_registered_at__isnull=False).count()
    if registered:
        results.append((True, f"Live M-Pesa: {registered} Paybill/Till(s) on Daraja with callbacks registered."))
    elif daraja.exists():
        results.append((False, "Live Daraja keys are saved but Safaricom has not accepted the callback URLs: "
                               "open M-Pesa > Settings and register them again."))
    else:
        results.append((False, "No landlord has live M-Pesa yet: enter live Daraja keys in M-Pesa > Settings."))
    results.append(_platform_payments())
    results.append((True, "The assistant is offered (OPENAI_API_KEY is set)." if settings.OPENAI_API_KEY else
                    "The assistant is not offered (no OPENAI_API_KEY); that is fine."))
    return results


def _platform_payments() -> tuple[bool, str]:
    """How landlords pay us (D-126)."""
    from subscriptions import collect

    ways = []
    if collect.prompt_configured() or collect.mpesa_configured():
        live = settings.PLATFORM_DARAJA_ENVIRONMENT == "PRODUCTION"
        ways.append(f"our Paybill {settings.PLATFORM_DARAJA_SHORTCODE} on Daraja ({'live' if live else 'SANDBOX'})")
    if collect.paystack_configured():
        live = settings.PLATFORM_PAYSTACK_SECRET_KEY.startswith("sk_live_")
        ways.append(f"Paystack ({'live' if live else 'TEST key'})")
    if not ways:
        return (False, "Landlords cannot pay their subscription online: set the PLATFORM_DARAJA_* keys or "
                       "PLATFORM_PAYSTACK_SECRET_KEY (payments can still be recorded by hand in admin).")
    on = collect.methods()
    return (all("SANDBOX" not in w and "TEST" not in w for w in ways),
            f"Subscriptions are paid through {' and '.join(ways)}; offered now: "
            f"M-Pesa prompt {'on' if on['mpesa'] else 'off'}, Paystack {'on' if on['paystack'] else 'off'}.")


def checks(now=None) -> list[tuple[bool, str]]:
    now = now or timezone.now()
    results = [(not settings.DEBUG, "DEBUG is off." if not settings.DEBUG else "DEBUG is on.")]
    results += _tools()
    results += _database()
    results += _backups(now)
    results.append(_cache())
    results += _email()
    results += _providers()
    late = jobs.problems(now)
    results += [(False, f"Job {line}.") for line in late] or [(True, "Every scheduled job has run on time.")]
    return results
