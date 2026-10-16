from ai_assistant.services import purge
from core.jobs import JobCommand


class Command(JobCommand):
    help = "Delete questions asked of the assistant more than 90 days ago (D-091 item 7)."

    def run(self, *args, **options):
        return f"Deleted {purge()} questions."
