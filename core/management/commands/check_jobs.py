from django.core.management.base import BaseCommand

from core.alerts import check_and_alert
from core.jobs import purge_old_runs


class Command(BaseCommand):
    help = "Email OPS_ALERT_EMAILS if a scheduled job is late or failed or a health check fails. Run every 15 minutes."

    def handle(self, *args, **options):
        result = check_and_alert()
        purged = purge_old_runs()
        self.stdout.write(self.style.SUCCESS(f"{result}; old job runs removed: {purged}"))
