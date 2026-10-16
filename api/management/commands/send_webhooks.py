from api.webhooks import run
from core.jobs import JobCommand, summarize


class Command(JobCommand):
    help = "Turn new audit events into webhook deliveries, send the due ones and delete old ones. Run every 5 minutes."

    def run(self, *args, **options):
        return summarize(run())
