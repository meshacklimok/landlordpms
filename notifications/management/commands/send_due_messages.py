from core.jobs import JobCommand, summarize
from notifications.delivery import send_due


class Command(JobCommand):
    help = "Send queued messages that are due: held back by quiet hours or waiting to retry. Run every few minutes."

    def run(self, *args, **options):
        return summarize(send_due())
