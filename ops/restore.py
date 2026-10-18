"""Restore a backup into a scratch database and check it against the live one (D-098 items 1-2).

This proves the backups restore. Rebuilding on a separate machine is still tested by hand (doc 18).
"""

import shutil
import tarfile
import tempfile
import time
from pathlib import Path

import psycopg
from django.apps import apps
from django.conf import settings
from psycopg import sql

from .backup import NAME, BackupError, _pg_env, _run, backup_dir

# The tables whose row counts are reported, live against the copy.
KEY_MODELS = ["accounts.Organization", "properties.Unit", "tenants.Tenant", "leases.Lease", "billing.Invoice",
              "payments.Payment"]


class RestoreError(BackupError):
    pass


def scratch_name() -> str:
    return f"{settings.DATABASES['default']['NAME']}_restore_test"


def _connect(dbname: str) -> psycopg.Connection:
    db = settings.DATABASES["default"]
    return psycopg.connect(dbname=dbname, host=db.get("HOST") or "localhost", port=db.get("PORT") or 5432,
                           user=db.get("USER") or None, password=db.get("PASSWORD") or None, autocommit=True,
                           sslmode=db.get("OPTIONS", {}).get("sslmode", "prefer"),
                           application_name="landlordpms-restore")


def latest_set(folder: Path | None = None) -> tuple[Path, Path | None]:
    """The newest database dump in the backup folder, and the media archive made with it, if there is one."""
    folder = folder or backup_dir()
    dumps = sorted(p for p in folder.glob("db-*.dump") if NAME.match(p.name))
    if not dumps:
        raise RestoreError(f"No backup found in {folder}.")
    return dumps[-1], _media_for(dumps[-1])


def _media_for(dump: Path) -> Path | None:
    match = NAME.match(dump.name)
    media = dump.with_name(f"media-{match.group(2)}.tar.gz") if match else None
    return media if media is not None and media.exists() else None


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}


def _count(conn, table: str) -> int:
    return conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]


def _migrations(conn) -> set[tuple[str, str]]:
    return set(conn.execute("SELECT app, name FROM django_migrations").fetchall())


def _drop(admin, name: str) -> None:
    """A plain drop first: it waits for autovacuum, which a forced drop may not end for a role that is not a
    superuser (it fails with "permission denied to terminate process" just after a restore). Force only when a
    session of our own is still connected."""
    try:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
    except psycopg.errors.ObjectInUse:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def restore_test(dump: Path | str | None = None, *, keep: bool = False) -> dict:
    """Restores `dump` (default: the latest) into the scratch database, checks it, and drops it unless `keep`."""
    started = time.monotonic()
    live, target = settings.DATABASES["default"]["NAME"], scratch_name()
    if dump is None:
        dump, media = latest_set()
    else:
        dump = Path(dump)
        if not dump.is_file():
            raise RestoreError(f"{dump} does not exist.")
        media = _media_for(dump)
    args, env = _pg_env()
    server = args[: args.index("--dbname")]
    pg_restore = getattr(settings, "PG_RESTORE", "") or shutil.which("pg_restore") or "pg_restore"
    with _connect("postgres") as admin:
        _drop(admin, target)
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
    try:
        # pg_stat_statements and default privileges need a superuser and are server settings, not data: restore without.
        entries = _run([pg_restore, "--list", str(dump)], env).stdout.splitlines()
        wanted = [line for line in entries if "pg_stat_statements" not in line and " DEFAULT ACL " not in line]
        with tempfile.TemporaryDirectory() as tmp:
            use_list = Path(tmp) / "restore.list"
            use_list.write_text("\n".join(wanted) + "\n", encoding="utf-8")
            _run([pg_restore, "--no-owner", "--exit-on-error", "--use-list", str(use_list), *server,
                  "--dbname", target, str(dump)], env)
        with _connect(target) as copy, _connect(live) as source:
            missing = sorted(_tables(source) - _tables(copy))
            if missing:
                raise RestoreError(f"Tables missing from the backup: {', '.join(missing[:10])}.")
            if not _count(copy, "django_migrations"):
                raise RestoreError("The backup has no migrations recorded: it is empty.")
            counts = {}
            for label in KEY_MODELS:
                table = apps.get_model(label)._meta.db_table
                counts[label.split(".")[1]] = (_count(copy, table), _count(source, table))
            if all(c == 0 for c, _l in counts.values()) and any(n for _c, n in counts.values()):
                raise RestoreError("The backup has no data, but the live database has.")
            behind = sorted(_migrations(source) - _migrations(copy))
    finally:
        if not keep:
            with _connect("postgres") as admin:
                _drop(admin, target)
    media_files = None
    if media is not None:
        try:
            with tarfile.open(media) as tar:
                media_files = sum(1 for member in tar if member.isfile())
        except (tarfile.TarError, OSError) as e:
            raise RestoreError(f"The media archive {media.name} cannot be read: {e}") from e
    return {"backup": dump.name, "seconds": round(time.monotonic() - started, 1), "counts": counts,
            "migrations_behind": [f"{app}.{name}" for app, name in behind], "media_files": media_files,
            "kept": target if keep else ""}


def summary(result: dict) -> str:
    """One line for the job record and the restore log in doc 18."""
    counts = ", ".join(f"{name} {copy}/{live}" for name, (copy, live) in result["counts"].items())
    parts = [f"{result['backup']} restored in {result['seconds']}s", f"rows backup/live: {counts}"]
    parts.append("no media archive" if result["media_files"] is None else f"media files: {result['media_files']}")
    if result["migrations_behind"]:
        parts.append(f"backup is {len(result['migrations_behind'])} migrations behind live")
    if result["kept"]:
        parts.append(f"kept as {result['kept']}")
    return "; ".join(parts)
