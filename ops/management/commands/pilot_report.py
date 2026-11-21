import csv
import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import Organization
from core import exports
from ops import pilot
from support.models import AppOpen

COLUMNS = ["organization", "setup_minutes", "invoices", "payments", "collected", "auto", "by_hand", "waiting",
           "auto_share", "staff_active", "staff", "app_opens", "app_android", "app_ios", "misses"]


class Command(BaseCommand):
    help = "Each organization against the pilot's success measures in doc 02, for one month (D-101)."

    def add_arguments(self, parser):
        parser.add_argument("--month", help="YYYY-MM; the last full month by default.")
        parser.add_argument("--org", action="append", default=[], help="An organization's public id (repeatable).")
        parser.add_argument("--csv", action="store_true", help="Write CSV instead of a table.")

    def handle(self, *args, **options):
        if options["month"]:
            try:
                month = datetime.datetime.strptime(options["month"], "%Y-%m").date()
            except ValueError as e:
                raise CommandError("--month must look like 2026-10.") from e
        else:
            month = (timezone.localdate().replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        orgs = Organization.objects.all()
        if options["org"]:
            orgs = orgs.filter(public_id__in=options["org"])
        rows = [self._row(s) for s in pilot.scores(month, organizations=orgs)]
        if options["csv"]:
            writer = csv.DictWriter(self.stdout, fieldnames=COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows({k: exports.safe(v) for k, v in row.items()} for row in rows)
            return
        self.stdout.write(f"Pilot measures for {month:%B %Y}: set-up under {pilot.SETUP_MINUTES} minutes, "
                          f"rent cycle in the app, {pilot.AUTO_SHARE}%+ matched without work.")
        for row in rows:
            share = "-" if row["auto_share"] is None else f"{row['auto_share']}%"
            setup = "not yet" if row["setup_minutes"] is None else f"{row['setup_minutes']} min"
            self.stdout.write(
                f"{row['organization']}: set-up {setup}; {row['invoices']} invoices, {row['payments']} payments "
                f"(KES {row['collected']:,}); matched on its own {share} ({row['auto']} auto, {row['by_hand']} by "
                f"hand, {row['waiting']} waiting); staff active {row['staff_active']}/{row['staff']}; "
                f"installed app opened {row['app_opens']} times "
                f"(Android {row['app_android']}, iPhone {row['app_ios']}); "
                + (f"misses: {row['misses']}" if row["misses"] else "meets all"))
        if not rows:
            self.stdout.write("No organizations.")
        for audience, platforms in pilot.opens_outside_organizations(month).items():
            self.stdout.write(f"{AppOpen.Audience(audience).label} opened the installed app {sum(platforms.values())} "
                              f"times (Android {platforms.get('ANDROID', 0)}, iPhone {platforms.get('IOS', 0)}).")

    @staticmethod
    def _row(score: pilot.Score) -> dict:
        row = {name: getattr(score, name) for name in COLUMNS if name not in ("misses",)}
        row["misses"] = ", ".join(score.misses)
        return row
