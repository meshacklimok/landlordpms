import json
import secrets
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core import scale
from leases.models import Lease
from mpesa import services as mpesa_services
from portal.models import TenantAccount

# Senders that would reach a real phone. The test tenants' numbers are made up and may belong to someone.
REAL_SENDERS = {
    "SMS_BACKEND": ("core.sms.AfricasTalkingSmsSender",),
    "WHATSAPP_BACKEND": ("core.whatsapp.CloudApiWhatsAppSender",),
}


class Command(BaseCommand):
    help = ("Build an organization for the load test and write the sessions and M-Pesa callback address that "
            "loadtest/locustfile.py uses (D-105, doc 20 §30). The rows stay: run it on a database you will "
            "throw away, never the live one.")

    def add_arguments(self, parser):
        parser.add_argument("--leases", type=int, default=200, help="Units with active leases to build.")
        parser.add_argument("--staff", type=int, default=10, help="Staff sessions (all the organization's owner).")
        parser.add_argument("--tenants", type=int, default=50, help="Tenants given a portal login and session.")
        parser.add_argument("--out", default="loadtest/setup.json", help="Where to write what Locust needs.")
        parser.add_argument("--confirm", action="store_true",
                            help="Needed when DEBUG is off: the rows written cannot be removed afterwards.")

    def handle(self, *args, **options):
        for name, real in REAL_SENDERS.items():
            if getattr(settings, name, "") in real:
                raise CommandError(f"{name} sends to real phones. Point it at a memory or console sender first; "
                                   "the load test's tenants have made-up numbers that may belong to someone.")
        if not settings.DEBUG and not options["confirm"]:
            raise CommandError("This writes thousands of rows that cannot be deleted (the ledger is append-only). "
                               "Run it on a throwaway copy, such as one made by `restore_test --keep`, with "
                               "--confirm.")
        n, out = options["leases"], self.stdout.write
        if options["tenants"] > n:
            raise CommandError("--tenants cannot be more than --leases.")

        with scale.quiet():
            owner = scale.make_owner(f"Load test {timezone.now():%Y-%m-%d %H:%M}")
            prop = scale.make_property(owner)
            for i in range(1, n + 1):
                scale.add_lease(owner, prop, i)
                if i % 50 == 0:
                    out(f"  built {i} of {n} leases")
            account = mpesa_services.add_account(owner, type="PAYBILL", number="600999", display_name="Load test")
            creds = mpesa_services.save_credentials(owner, account, environment="SANDBOX",
                                                    consumer_key="load-test", consumer_secret="load-test")  # noqa: S106

        leases = list(Lease.objects.filter(unit__property=prop).select_related("unit")
                      .prefetch_related("lease_tenants__tenant").order_by("pk"))
        tenants = []
        for lease in leases[:options["tenants"]]:
            tenant = lease.primary_tenant
            user = User.objects.create_user(phone="+2547" + "".join(secrets.choice("0123456789") for _ in range(8)),
                                            password=secrets.token_urlsafe(16), full_name=tenant.name)
            user.phone_verified_at = timezone.now()
            user.save(update_fields=["phone_verified_at"])
            TenantAccount.objects.create(organization=owner.organization, tenant=tenant, user=user)
            tenants.append({"session": _session(user), "lease": str(lease.public_id)})

        data = {
            "organization": owner.organization.name,
            "session_cookie": settings.SESSION_COOKIE_NAME,
            "staff_sessions": [_session(owner.user) for _ in range(options["staff"])],
            "staff_pages": scale.LISTS,
            "lease_pages": [reverse("billing:lease_account", args=[lease.public_id]) for lease in leases],
            "tenants": tenants,
            "callback": reverse("hook_daraja", args=[creds.callback_token, "confirm"]),
            "shortcode": account.number,
            "references": [lease.unit.payment_reference for lease in leases],
        }
        path = Path(options["out"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        out(self.style.SUCCESS(f"Built \"{data['organization']}\" with {n} leases, {options['staff']} staff and "
                               f"{len(tenants)} tenant sessions. Wrote {path}; it holds live sessions, so delete it "
                               "when done."))


def _session(user) -> str:
    client = Client()
    client.force_login(user)
    return client.cookies[settings.SESSION_COOKIE_NAME].value
