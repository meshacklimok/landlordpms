from django.core.management.base import BaseCommand

from notifications.delivery import send_due


class Command(BaseCommand):
    help = "Send queued messages that are due: held back by quiet hours or waiting to retry. Run every few minutes."

    def handle(self, *args, **options):
        counts = send_due()
        self.stdout.write(self.style.SUCCESS(", ".join(f"{k}: {v}" for k, v in counts.items())))
