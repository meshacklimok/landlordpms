from core.backup import make_backup
from core.jobs import JobCommand, summarize


class Command(JobCommand):
    help = "Back up the database (pg_dump) and media to BACKUP_DIR, check the dump and prune old sets."

    def run(self, *args, **options):
        return summarize(make_backup())
