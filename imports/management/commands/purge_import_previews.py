from core.jobs import JobCommand
from imports.services import purge_stale_previews


class Command(JobCommand):
    help = "Discard import previews older than a day, dropping the uploaded rows."

    def run(self, *args, **options):
        return f"Discarded {purge_stale_previews()} previews."
