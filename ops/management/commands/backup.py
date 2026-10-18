import subprocess
from pathlib import Path

from django.conf import settings

from core.jobs import JobCommand, summarize
from ops.backup import BackupError, make_backup


class Command(JobCommand):
    help = ("Back up the database (pg_dump) and media to BACKUP_DIR, check the dump and prune old sets. "
            "Run it at any time; --offsite also copies the result off the server straight away.")

    def add_arguments(self, parser):
        parser.add_argument("--offsite", action="store_true",
                            help="After the backup, run deploy/offsite.sh to copy it to the off-site storage now.")

    def run(self, *args, **options):
        counts = make_backup()
        if options.get("offsite"):
            script = Path(settings.BASE_DIR) / "deploy" / "offsite.sh"
            # noqa below: a fixed script path from settings, never a shell string.
            result = subprocess.run(["sh", str(script)], capture_output=True, text=True, timeout=3600)  # noqa: S603, S607
            if result.returncode != 0:
                raise BackupError(f"Backup made, but the off-site copy failed: {result.stderr.strip()[-300:]}")
            counts["offsite"] = (result.stdout.strip() or "copied")[:80]
        return summarize(counts)
