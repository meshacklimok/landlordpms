from django.core.management.base import BaseCommand

from billing.jobs import run_daily


class Command(BaseCommand):
    help = "Bill the current (and, within the lead days, next) month; mark moved-out tenants; archive settled leases."

    def handle(self, *args, **options):
        counts = run_daily()
        self.stdout.write(self.style.SUCCESS(", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in counts.items())))
