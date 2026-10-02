from core.jobs import JobCommand, summarize
from subscriptions.services import daily


class Command(JobCommand):
    help = "End trials, issue renewal invoices, mark unpaid subscriptions due and lapse them after the grace days."

    def run(self, *args, **options):
        return summarize(daily())
