"""An organization with many leases, built through the services, for the query-count test and `scale_test`
(D-097 item 4, D-105). Nothing here sends a message: callers switch the senders to memory first."""

import datetime
import secrets
import time
from contextlib import contextmanager

from django.conf import settings
from django.db import connection, reset_queries
from django.test import Client
from django.test.utils import CaptureQueriesContext, override_settings
from django.utils import timezone

from accounts import services as account_services
from accounts.models import Membership, Organization, User
from billing import invoicing
from expenses import services as expense_services
from leases import services as lease_services
from maintenance import services as maintenance_services
from payments import services as payment_services
from properties import services as property_services
from properties.models import Property
from prospects import services as prospect_services
from subscriptions.models import Plan
from subscriptions.services import subscription_for
from tenants import services as tenant_services

# The staff pages that list rows. Each must ask the database the same number of times however many rows it shows.
LISTS = ["/", "/properties/", "/properties/units/", "/tenants/", "/leases/", "/billing/invoices/", "/billing/arrears/",
         "/billing/call-list/", "/payments/", "/payments/review/", "/mpesa/inbox/", "/mpesa/transactions/",
         "/expenses/", "/expenses/suppliers/", "/maintenance/", "/meters/", "/meters/readings/", "/documents/",
         "/prospects/", "/reports/", "/reports/owner-statement/", "/properties/owners/", "/messages/", "/staff/"]

# Nothing built here reaches a tenant, a phone or a provider.
QUIET = {
    "SMS_BACKEND": "notifications.providers.sms.MemorySmsSender",
    "PUSH_BACKEND": "notifications.push.MemoryPushSender",
    "WHATSAPP_BACKEND": "",
    "MAILERS": {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}},
    "MPESA_CLIENT": "mpesa.daraja.FakeDarajaClient",
    "PAYSTACK_CLIENT": "subscriptions.paystack.FakePaystackClient",
    "ASSISTANT_BACKEND": "ai_assistant.client.FakeClient",
}


def make_owner(name: str = "Scale test") -> Membership:
    phone = "+2547" + "".join(secrets.choice("0123456789") for _ in range(8))
    user = User.objects.create_user(phone=phone, password=secrets.token_urlsafe(16), full_name="Scale test owner")
    user.phone_verified_at = timezone.now()
    user.save(update_fields=["phone_verified_at"])
    owner = account_services.create_organization(user=user, name=name, org_type=Organization.Type.COMPANY)
    # The trial plan stops at 100 units; the test needs room for more.
    sub = subscription_for(owner.organization)
    sub.plan = Plan.objects.filter(unit_limit__isnull=True).first() or Plan.objects.order_by("-unit_limit").first()
    sub.save(update_fields=["plan"])
    return owner


def make_property(owner: Membership, name: str = "Scale Court") -> Property:
    return Property.objects.create(organization=owner.organization, name=name, code="SC1")


def add_lease(owner: Membership, prop: Property, n: int):
    """One unit with a tenant, an active lease, an invoice, a part payment, a repair, an expense and a prospect."""
    unit = property_services.create_unit(owner, prop, code=f"U{n}")
    tenant = tenant_services.create_tenant(owner, name=f"Tenant {n}", phone=f"07129{n:05d}")
    lease = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=datetime.date(2026, 1, 1),
                                        end_date=None, rent=20000, due_day=5, grace_days=3)
    lease = lease_services.activate_lease(owner, lease)
    invoicing.generate_lease_period(lease, datetime.date(2026, 2, 1), today=datetime.date(2026, 1, 27))
    payment_services.record_payment(owner, lease, amount=5000, method="CASH", paid_at=datetime.date(2026, 2, 3))
    maintenance_services.report(owner, prop, title=f"Leak {n}", unit=unit)
    expense_services.record_expense(owner, prop, category=expense_services.categories(owner.organization).first(),
                                    description="Gate", amount="100", paid_on=datetime.date(2026, 2, 1), method="CASH")
    prospect_services.create_prospect(owner, prop, name=f"Prospect {n}", phone=f"07139{n:05d}")


@contextmanager
def quiet():
    with override_settings(**QUIET):
        yield


def logged_in_client(owner: Membership) -> tuple[Client, dict]:
    """A client for this server's settings: its own host name, and https when the site redirects to it."""
    hosts = [h.lstrip(".") for h in settings.ALLOWED_HOSTS if h != "*"] or ["testserver"]
    client = Client(SERVER_NAME=hosts[0])
    client.force_login(owner.user)
    return client, {"secure": getattr(settings, "SECURE_SSL_REDIRECT", False)}


def time_pages(client: Client, pages: list[str], **kwargs) -> list[dict]:
    rows = []
    for page in pages:
        reset_queries()  # the log keeps 9000 queries; building the data fills it
        with CaptureQueriesContext(connection) as ctx:
            started = time.perf_counter()
            response = client.get(page, **kwargs)
            ms = (time.perf_counter() - started) * 1000
        rows.append({"page": page, "status": response.status_code, "ms": ms, "queries": len(ctx.captured_queries),
                     "query_ms": sum(float(q["time"]) for q in ctx.captured_queries) * 1000})
    return rows
