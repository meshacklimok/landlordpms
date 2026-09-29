from django.core.management.base import BaseCommand

from accounts.services import sync_access_catalog


class Command(BaseCommand):
    help = "Sync capabilities from accounts/capabilities.py and seed missing role templates."

    def handle(self, *args, **options):
        stats = sync_access_catalog()
        self.stdout.write(self.style.SUCCESS(", ".join(f"{k}: {v}" for k, v in stats.items())))
