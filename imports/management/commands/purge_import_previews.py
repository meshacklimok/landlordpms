from banking.services import purge_stale_previews as purge_statements
from core.jobs import JobCommand
from imports.services import purge_stale_previews


class Command(JobCommand):
    help = "Discard import and statement previews older than a day, dropping the uploaded rows."

    def run(self, *args, **options):
        return f"Discarded {purge_stale_previews()} import previews and {purge_statements()} statement previews."
