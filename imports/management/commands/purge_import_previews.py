from django.core.management.base import BaseCommand

from imports.services import purge_stale_previews


class Command(BaseCommand):
    help = "Discard import previews older than a day, dropping the uploaded rows."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f"Discarded {purge_stale_previews()} previews."))
