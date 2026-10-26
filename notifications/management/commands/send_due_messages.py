from core.jobs import JobCommand, summarize
from notifications import push
from notifications.delivery import check_celcom_reports, send_due


class Command(JobCommand):
    help = ("Send queued messages that are due: held back by quiet hours or waiting to retry. Run every few minutes."
            " Also pushes alerts written by bulk jobs to staff phones (D-089).")

    def run(self, *args, **options):
        return summarize({**send_due(), **push.send_due(), **check_celcom_reports()})
