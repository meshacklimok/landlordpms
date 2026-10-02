from core.jobs import JobCommand, summarize
from mpesa.jobs import run_daily


class Command(JobCommand):
    help = "Retry stuck M-Pesa transactions, check waiting payment requests and send yesterday's summary."

    def run(self, *args, **options):
        return summarize(run_daily())
