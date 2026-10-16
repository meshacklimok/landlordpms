from django.contrib.sessions.models import Session
from django.utils import timezone

from core.jobs import JobCommand


class Command(JobCommand):
    help = "Delete expired login sessions, which Django never removes on its own (D-105)."

    def run(self, *args, **options):
        deleted, _ = Session.objects.filter(expire_date__lt=timezone.now()).delete()
        return f"Deleted {deleted} expired sessions."
