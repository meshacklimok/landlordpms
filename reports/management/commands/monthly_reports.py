from core.jobs import JobCommand, summarize
from reports.monthly import send_all


class Command(JobCommand):
    help = "Email last month's property report to each member who can see financial reports (D-194)."

    def run(self, *args, **options):
        return summarize(send_all())
