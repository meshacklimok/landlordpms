"""Nightly backups: a pg_dump and the media folder, checked and pruned (D-062 item 3).

Copying the files off the server is the operator's job (doc 18); this keeps the local sets.
"""

import datetime
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

from django.conf import settings
from django.utils import timezone

NAME = re.compile(r"^(db|media)-(\d{8}-\d{6})\.(dump|tar\.gz)$")
KEEP_DAILY, KEEP_WEEKLY, KEEP_MONTHLY = 7, 4, 12


class BackupError(Exception):
    pass


def backup_dir() -> Path:
    path = Path(getattr(settings, "BACKUP_DIR", "") or Path(settings.BASE_DIR) / "backups")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _pg_env() -> tuple[list[str], dict]:
    db = settings.DATABASES["default"]
    args = ["--host", db.get("HOST") or "localhost", "--port", str(db.get("PORT") or 5432),
            "--username", db.get("USER") or "", "--dbname", db["NAME"]]
    env = {**os.environ, "PGPASSWORD": db.get("PASSWORD") or ""}
    return args, env


def _run(cmd: list[str], env: dict) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=3600)
    except FileNotFoundError as e:
        raise BackupError(f"{cmd[0]} is not installed or not on PATH") from e
    if result.returncode != 0:
        raise BackupError(f"{cmd[0]} failed: {result.stderr.strip()[-500:]}")
    return result


def make_backup(now: datetime.datetime | None = None) -> dict:
    """Writes db-<stamp>.dump and media-<stamp>.tar.gz, checks the dump, and prunes old sets."""
    stamp = timezone.localtime(now or timezone.now()).strftime("%Y%m%d-%H%M%S")
    folder = backup_dir()
    dump = folder / f"db-{stamp}.dump"
    args, env = _pg_env()
    pg_dump = getattr(settings, "PG_DUMP", "") or shutil.which("pg_dump") or "pg_dump"
    pg_restore = getattr(settings, "PG_RESTORE", "") or shutil.which("pg_restore") or "pg_restore"
    try:
        _run([pg_dump, "--format=custom", "--no-owner", "--file", str(dump), *args], env)
        listing = _run([pg_restore, "--list", str(dump)], env)
    except BackupError:
        dump.unlink(missing_ok=True)
        raise
    if "TABLE DATA" not in listing.stdout:
        dump.unlink(missing_ok=True)
        raise BackupError("The dump has no table data.")
    media = folder / f"media-{stamp}.tar.gz"
    media_root = Path(settings.MEDIA_ROOT)
    with tarfile.open(media, "w:gz") as tar:
        if media_root.exists():
            tar.add(media_root, arcname="media")
    removed = prune(folder)
    return {"database": dump.name, "database_bytes": dump.stat().st_size, "media_bytes": media.stat().st_size,
            "removed": len(removed)}


def keep(stamps: list[str]) -> set[str]:
    """Which stamps to keep: the last 7 days, and the first day of each of the last 4 weeks and 12 months."""
    days = {}
    for stamp in sorted(set(stamps)):
        days[stamp[:8]] = stamp  # each day's latest
    by_day = sorted(days.items(), reverse=True)
    kept = {s for _d, s in by_day[:KEEP_DAILY]}
    weeks, months = {}, {}
    for day, stamp in sorted(days.items()):
        date = datetime.datetime.strptime(day, "%Y%m%d").date()
        weeks.setdefault(date.isocalendar()[:2], stamp)
        months.setdefault((date.year, date.month), stamp)
    kept |= set(sorted(weeks.values(), reverse=True)[:KEEP_WEEKLY])
    kept |= set(sorted(months.values(), reverse=True)[:KEEP_MONTHLY])
    return kept


def prune(folder: Path) -> list[str]:
    files = [(p, NAME.match(p.name)) for p in folder.iterdir() if p.is_file()]
    files = [(p, m) for p, m in files if m]
    wanted = keep([m.group(2) for _p, m in files])
    removed = []
    for path, match in files:
        if match.group(2) not in wanted:
            path.unlink()
            removed.append(path.name)
    return removed
