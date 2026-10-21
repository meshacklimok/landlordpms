from core.jobs import JobCommand
from notifications.services import clear_old_text


class Command(JobCommand):
    help = "Clear the text and address of messages older than MESSAGE_TEXT_DAYS, keeping the rows (D-105)."

    def run(self, *args, **options):
        return f"Cleared {clear_old_text()} messages."
