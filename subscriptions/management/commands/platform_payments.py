"""Our own Paybill and Paystack account for subscription payments (D-126).

    python manage.py platform_payments            # what is set up, and whether the keys work
    python manage.py platform_payments --register # tell Safaricom where to send Paybill payments
"""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from mpesa import daraja, paystack
from subscriptions import collect


class Command(BaseCommand):
    help = "Check our Daraja and Paystack keys for subscription payments; --register registers the C2B URLs."

    def add_arguments(self, parser):
        parser.add_argument("--register", action="store_true", help="Register the Paybill's C2B callback URLs.")

    def handle(self, *args, **options):
        switch = collect.methods()
        if collect.mpesa_configured():
            creds = collect.PlatformDaraja()
            try:
                daraja.get_client(creds).token()
                self.stdout.write(f"Daraja ({creds.environment}, shortcode {creds.shortcode}): keys work.")
            except daraja.DarajaError as e:
                self.stdout.write(self.style.ERROR(f"Daraja refused the keys: {e}"))
            if not collect.prompt_configured():
                self.stdout.write("  No PLATFORM_DARAJA_PASSKEY: Paybill payments are matched, but no M-Pesa prompt.")
            self.stdout.write(f"  Paybill confirmations go to {collect.callback_url('confirm')}")
        else:
            self.stdout.write("Daraja: not set up (PLATFORM_DARAJA_CONSUMER_KEY and friends).")
        if collect.paystack_configured():
            try:
                paystack.get_client(collect.PlatformPaystack()).check()
                self.stdout.write("Paystack: key works.")
            except paystack.PaystackError as e:
                self.stdout.write(self.style.ERROR(f"Paystack refused the key: {e}"))
            self.stdout.write(f"  Webhook URL for the Paystack dashboard: {collect.callback_url('ps')}")
        else:
            self.stdout.write("Paystack: not set up (PLATFORM_PAYSTACK_SECRET_KEY).")
        self.stdout.write(f"Offered on the subscription page now: M-Pesa prompt {'yes' if switch['mpesa'] else 'no'}, "
                          f"Paystack {'yes' if switch['paystack'] else 'no'} (switches in Django admin).")
        if options["register"]:
            try:
                collect.register_urls()
            except ValidationError as e:
                raise CommandError(" ".join(e.messages)) from None
            self.stdout.write(self.style.SUCCESS("Safaricom accepted the Paybill's callback URLs."))
