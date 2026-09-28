from django.core.management.base import BaseCommand

from mpesa.jobs import run_daily


class Command(BaseCommand):
    help = "Retry stuck M-Pesa transactions, check waiting payment requests and send yesterday's summary."

    def handle(self, *args, **options):
        counts = run_daily()
        self.stdout.write(self.style.SUCCESS(", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in counts.items())))
