from accounts.otp import purge_expired
from core.jobs import JobCommand


class Command(JobCommand):
    help = "Delete one-time codes older than a day."

    def run(self, *args, **options):
        return f"Deleted {purge_expired()} codes."
