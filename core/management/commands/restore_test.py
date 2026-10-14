from core.jobs import JobCommand
from core.restore import restore_test, summary


class Command(JobCommand):
    help = ("Restore the latest backup into a scratch database, check it against the live one, then drop it. "
            "Run monthly from cron (D-098).")

    def add_arguments(self, parser):
        parser.add_argument("--file", help="A db-*.dump file to restore instead of the latest.")
        parser.add_argument("--keep", action="store_true", help="Keep the scratch database to look at.")

    def run(self, *args, **options):
        return summary(restore_test(options.get("file"), keep=options.get("keep", False)))
