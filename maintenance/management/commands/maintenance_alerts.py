from core.jobs import JobCommand, summarize
from maintenance.services import send_late_alerts


class Command(JobCommand):
    help = "Tell staff about maintenance jobs past their respond-by or fix-by time (D-074 item 7)."

    def run(self, *args, **options):
        return summarize(send_late_alerts())
