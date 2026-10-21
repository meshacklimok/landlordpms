from core.jobs import JobCommand, summarize
from growth.referrals import check_pending
from subscriptions.collect import charge_due_cards, give_up_waiting
from subscriptions.services import daily, send_reminders


class Command(JobCommand):
    help = ("End trials, issue renewal invoices, mark unpaid subscriptions due and lapse them after the grace days; "
            "close online payments nobody answered; charge saved cards for renewals; email the reminders set in "
            "the billing policy; reward the "
            "referrals whose invited landlord has now done what the referral policy asks.")

    def run(self, *args, **options):
        # Cards are charged after the renewal invoices exist and before reminders, so a reminder never says
        # "pay" for an invoice the card has just settled.
        return summarize({**daily(), "checkouts_closed": give_up_waiting(), **charge_due_cards(),
                          "reminders": send_reminders(),
                          "referrals_rewarded": check_pending()})
