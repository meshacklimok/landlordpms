from console.owners import run_due
from core.jobs import JobCommand, summarize


class Command(JobCommand):
    help = "Send the emails to owners that are waiting in the queue, a few minutes at a time (D-179)."

    def run(self, *args, **options):
        return summarize(run_due())
