from django.core.management.base import BaseCommand

from accounts.otp import purge_expired


class Command(BaseCommand):
    help = "Delete one-time codes older than a day."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f"Deleted {purge_expired()} codes."))
