from django.core.management.base import BaseCommand, CommandError

from accounts.models import Organization
from reports.snapshots import backfill


class Command(BaseCommand):
    help = ("Rebuild daily arrears snapshots for past days from the ledger as recorded now (D-077). "
            "Days the daily job already recorded are kept.")

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=365)
        parser.add_argument("--org", type=int, help="One organization id; all of them when left out.")

    def handle(self, *args, days, org, **options):
        if not 1 <= days <= 3 * 366:
            raise CommandError("--days must be between 1 and 1098.")
        orgs = Organization.objects.order_by("pk")
        if org is not None:
            orgs = orgs.filter(pk=org)
            if not orgs.exists():
                raise CommandError(f"No organization {org}.")
        for o in orgs:
            rows = backfill(o, days)
            self.stdout.write(f"{o.pk} {o.display_name}: {rows} rows")
