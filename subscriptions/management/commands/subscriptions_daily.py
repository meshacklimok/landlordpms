from django.core.management.base import BaseCommand

from subscriptions.services import daily


class Command(BaseCommand):
    help = "End trials, issue renewal invoices, mark unpaid subscriptions due and lapse them after the grace days."

    def handle(self, *args, **options):
        counts = daily()
        self.stdout.write(self.style.SUCCESS(", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in counts.items())))
