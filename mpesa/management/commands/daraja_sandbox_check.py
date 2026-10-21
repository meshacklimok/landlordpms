from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.phone import InvalidPhoneNumber, normalize_phone
from mpesa import sandbox
from mpesa.models import DarajaCredentials


class Command(BaseCommand):
    help = ("Asks the Daraja sandbox the open M-Pesa questions and prints the answers next to what the code assumes "
            "(D-104). Sends test payments: sandbox credentials only.")

    def add_arguments(self, parser):
        parser.add_argument("--account", help="The M-Pesa payment account's public id (needed unless only --only sms).")
        parser.add_argument("--phone", default="", help="A Safaricom number for the payment prompts (stk-*, limit).")
        parser.add_argument("--reference", default="", help="A unit's payment reference to pay to (default: the "
                            "first active lease this account collects for).")
        parser.add_argument("--wait", type=int, default=90, help="Seconds to wait for each callback (default 90).")
        parser.add_argument("--only", default="", help=f"Comma-separated steps, from: {', '.join(sandbox.STEPS)}.")
        parser.add_argument("--site-url", default="", help="The address used for the SMS length (default SITE_URL).")
        parser.add_argument("--out", default="", help="Also write the answers as a Markdown table to this file.")

    def handle(self, *args, **options):
        steps = [s.strip() for s in options["only"].split(",") if s.strip()] or list(sandbox.STEPS)
        unknown = sorted(set(steps) - set(sandbox.STEPS))
        if unknown:
            raise CommandError(f"Unknown step(s): {', '.join(unknown)}. Steps: {', '.join(sandbox.STEPS)}.")
        creds = None
        if options["account"]:
            creds = (DarajaCredentials.objects.select_related("payment_account")
                     .filter(payment_account__public_id=options["account"]).first())
            if creds is None:
                raise CommandError("No Daraja credentials for that payment account.")
        elif set(steps) & sandbox.NEEDS_KEYS:
            raise CommandError("--account is needed for every step but sms.")
        phone = ""
        if options["phone"]:
            try:
                phone = normalize_phone(options["phone"])
            except InvalidPhoneNumber as e:
                raise CommandError("--phone is not a phone number.") from e
        ctx = sandbox.Context(creds=creds, phone=phone, reference=options["reference"], wait=options["wait"],
                              site_url=options["site_url"])
        try:
            findings = sandbox.run(ctx, steps)
        except sandbox.SandboxOnly as e:
            raise CommandError(str(e)) from e
        for f in findings:
            self.stdout.write(f"[{f.verdict}] {f.step}: {f.question}\n    {f.answer}")
            if f.assumption:
                self.stdout.write(f"    The code assumes: {f.assumption}")
        if options["out"]:
            Path(options["out"]).write_text(sandbox.markdown(findings), encoding="utf-8")
            self.stdout.write(f"Wrote {options['out']}.")
        if any(f.verdict == "differs" for f in findings):
            self.stdout.write("Some answers differ from what the code assumes: record them in a decision (doc 08).")
