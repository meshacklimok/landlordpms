from accounts.data_export import run_due
from core.jobs import JobCommand, summarize


class Command(JobCommand):
    help = "Make the organization data ZIPs people asked for, text them the link, and delete expired ones (D-177)."

    def run(self, *args, **options):
        return summarize(run_due())
