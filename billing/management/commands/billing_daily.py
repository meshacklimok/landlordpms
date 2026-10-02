from billing.jobs import run_daily
from core.jobs import JobCommand, summarize


class Command(JobCommand):
    help = "Bill the current (and, within the lead days, next) month; mark moved-out tenants; archive settled leases."

    def run(self, *args, **options):
        return summarize(run_daily())
