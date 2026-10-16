from django.core.management.base import BaseCommand

from notifications.push import make_keys


class Command(BaseCommand):
    help = "Print a new VAPID key pair for alerts on staff phones (D-089). Keep the private key secret."

    def handle(self, *args, **options):
        public, private = make_keys()
        self.stdout.write(f"VAPID_PUBLIC_KEY={public}\nVAPID_PRIVATE_KEY={private}")
