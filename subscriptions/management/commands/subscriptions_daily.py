from core.jobs import JobCommand, summarize
from subscriptions.collect import give_up_waiting
from subscriptions.services import daily


class Command(JobCommand):
    help = ("End trials, issue renewal invoices, mark unpaid subscriptions due and lapse them after the grace days; "
            "close online payments nobody answered.")

    def run(self, *args, **options):
        return summarize({**daily(), "checkouts_closed": give_up_waiting()})
