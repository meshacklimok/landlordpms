import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core import scale


class _Undo(Exception):
    pass


class Command(BaseCommand):
    help = ("Build one organization with many leases, time the main pages and count their queries, then undo "
            "everything (D-105, doc 20 §30). Run on staging or a restored copy, not the live database.")

    def add_arguments(self, parser):
        parser.add_argument("--leases", type=int, default=200, help="How many units with leases to build.")
        parser.add_argument("--slow-ms", type=int, default=1000, help="A page slower than this fails the test.")
        parser.add_argument("--confirm", action="store_true",
                            help="Needed when DEBUG is off: this writes to the database and rolls it back.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["confirm"]:
            raise CommandError("This writes thousands of rows, then rolls them back, while holding them in one "
                               "transaction. Run it on staging or a restored copy (restore_test --keep), "
                               "with --confirm.")
        n, out = options["leases"], self.stdout.write
        rows: list[dict] = []
        try:
            with scale.quiet(), transaction.atomic():
                owner = scale.make_owner()
                prop = scale.make_property(owner)
                started = time.perf_counter()
                for i in range(1, n + 1):
                    scale.add_lease(owner, prop, i)
                    if i % 50 == 0:
                        out(f"  built {i} of {n} leases")
                out(f"Built {n} leases in {time.perf_counter() - started:.1f}s.")
                client, kwargs = scale.logged_in_client(owner)
                pages = scale.LISTS + [f"/properties/{prop.public_id}/"]
                scale.time_pages(client, pages[:1], **kwargs)  # warm up
                rows = scale.time_pages(client, pages, **kwargs)
                client.logout()
                raise _Undo
        except _Undo:
            pass

        out(f"{'page':<40}{'status':>7}{'ms':>9}{'queries':>9}{'db ms':>9}")
        slow = []
        for row in rows:
            line = (f"{row['page'][:40]:<40}{row['status']:>7}{row['ms']:>9.0f}{row['queries']:>9}"
                    f"{row['query_ms']:>9.0f}")
            bad = row["status"] != 200 or row["ms"] > options["slow_ms"]
            out(self.style.ERROR(line) if bad else line)
            if bad:
                slow.append(row["page"])
        out("Everything built was rolled back.")
        if slow:
            raise CommandError(f"{len(slow)} page(s) failed or took over {options['slow_ms']} ms: {', '.join(slow)}")
        out(self.style.SUCCESS(f"Every page answered within {options['slow_ms']} ms with {n} leases."))
