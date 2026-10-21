from console import digest
from core.jobs import JobCommand


class Command(JobCommand):
    help = "Email the platform owner a short daily summary, unless it is switched off in System messages (D-216)."

    def run(self, *args, **options):
        return f"owner digest: {digest.send()}"
