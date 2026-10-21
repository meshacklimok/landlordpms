from core.jobs import JobCommand, summarize
from reports.snapshots import take_all


class Command(JobCommand):
    help = "Record what is overdue on every property today, for arrears over time (D-077)."

    def run(self, *args, **options):
        return summarize(take_all())
