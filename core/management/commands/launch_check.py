from django.core.management.base import BaseCommand, CommandError

from core.launch import REMINDERS, checks


class Command(BaseCommand):
    help = "List what the server still lacks before launch; fails until everything is done (D-098)."

    def handle(self, *args, **options):
        results = checks()
        for done, text in results:
            self.stdout.write((self.style.SUCCESS("  done  ") if done else self.style.ERROR("  TODO  ")) + text)
        self.stdout.write("")
        for text in REMINDERS:
            self.stdout.write(self.style.WARNING("  check ") + text)
        missing = sum(1 for done, _text in results if not done)
        if missing:
            raise CommandError(f"{missing} thing(s) still to do before launch.")
        self.stdout.write(self.style.SUCCESS("Ready, apart from the reminders above."))
