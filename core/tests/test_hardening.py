"""Pre-launch sweeps over every route (D-097).

One organization's records get the ids the sweep asks for: the first row of every table is number 500000 and has
the public id `ID`. Another organization's owner, and staff kept to one property, then open and post to every route
with those ids. Nothing marked "Canary" may show, nothing may change, and nothing may fail with a server error.
"""

import datetime
import uuid

import pytest
from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, models

from accounts.tests.factories import add_member, make_org, make_property
from audit.models import AuditEvent
from core import throttle
from core.tests.urlsweep import routes

pytestmark = pytest.mark.django_db

ID = uuid.UUID(int=500000)
MARKER = "Canary"

# Routes a visitor may reach without signing in (D-097 item 1).
PUBLIC = {
    "healthz", "csp_report", "vacancy", "hostel_stay", "receipt_link", "receipt_check", "invoice_check", "monthly_report_stop", "pay_link", "pay_link_status", "letter_check", "email_stop",
    "hook_africastalking", "hook_ussd", "hook_whatsapp", "hook_whatsapp_org", "hook_daraja", "hook_platform",
    "portal:accept",
    "owner_portal:accept",
    "support:manifest", "support:sw", "support:offline", "support:status", "support:security", "support:terms",
    "support:privacy", "growth:pricing", "growth:faq", "growth:tutorials", "growth:tutorial", "growth:demo",
    "growth:demo_thanks", "growth:demo_enter", "growth:demo_as", "growth:demo_leave", "growth:robots", "growth:sitemap", "growth:about", "growth:how_it_works",
    "growth:articles", "growth:article", "growth:solutions", "growth:solution", "growth:llms", "growth:llms_full",
    "growth:favicon", "growth:indexnow_key", "support:delete_account", "accounts:home", "accounts:web_csrf", "accounts:register", "accounts:login", "accounts:logout", "accounts:password_reset",
    "accounts:password_reset_confirm", "accounts:accept_invite", "esign_check", "esign_sign", "esign_sign_pdf",
}
# Public check pages whose random id is the key (they show face facts only, so a 200 is right).
KEYED_BY_RANDOM_ID = {"invoice_check"}
# Form fields that name a record, posted once as junk and once as the other organization's id.
ID_FIELDS = ["owner", "lease", "line", "charge_type", "category", "photo", "tenant", "letter", "reading", "payment",
             "request", "property", "viewing", "unit", "meter", "supplier", "invoice", "building", "member", "role",
             "document", "prospect", "announcement", "account", "signer"]


def _restart_ids():
    with connection.cursor() as cur:
        for model in apps.get_models():
            pk = model._meta.pk
            if isinstance(pk, models.AutoField) and model._meta.managed and not model._meta.proxy:
                cur.execute("SELECT pg_get_serial_sequence(%s, %s)", [model._meta.db_table, pk.column])
                sequence = cur.fetchone()[0]
                if sequence:
                    cur.execute("SELECT setval(%s, 499999)", [sequence])


def _give_first_rows_the_id():
    for model in apps.get_models():
        if not model._meta.proxy and any(f.name == "public_id" for f in model._meta.get_fields()):
            model._base_manager.filter(pk=500000).update(public_id=ID)


def _seed(owner, prop):
    from api import services as api_services
    from billing import invoicing
    from documents import services as document_services
    from esign import services as esign_services
    from expenses import services as expense_services
    from inspections import services as inspection_services
    from inspections.models import ConditionReport
    from leases import services as lease_services
    from maintenance import services as maintenance_services
    from meters import services as meter_services
    from payments import services as payment_services
    from properties import services as property_services
    from prospects import services as prospect_services
    from tenants import services as tenant_services

    # Owners and suppliers are one list for the whole organization (D-053, D-067), so their names carry no marker.
    landlord = property_services.create_owner(owner, name="Kamau Holdings", phone="0712999009")
    property_services.update_property(owner, prop, owner=landlord)
    property_services.create_building(owner, prop, name="Canary Block")
    unit = property_services.create_unit(owner, prop, code="CAN1")
    tenant = tenant_services.create_tenant(owner, name="Canary Tenant", phone="0712999001")
    lease = lease_services.create_lease(owner, unit=unit, tenants=[tenant], start_date=datetime.date(2026, 1, 1),
                                        end_date=None, rent=20000, due_day=5, grace_days=3)
    lease = lease_services.activate_lease(owner, lease)
    invoicing.generate_lease_period(lease, datetime.date(2026, 2, 1), today=datetime.date(2026, 1, 27))
    payment_services.record_payment(owner, lease, amount=5000, method="CASH", paid_at=datetime.date(2026, 2, 3))
    category = expense_services.categories(owner.organization).first()
    supplier = expense_services.create_supplier(owner, name="Juma Plumbing", phone="0712999003")
    expense_services.record_expense(owner, prop, category=category, description="Canary gate", amount="100",
                                    paid_on=datetime.date(2026, 2, 1), method="CASH", supplier=supplier)
    maintenance_services.report(owner, prop, title="Canary leak", unit=unit)
    meter = meter_services.create_meter(owner, prop, units=[(unit, "1")], label="CANM", serial="", kind="POSTPAID",
                                        split="EQUAL", rate="100", minimum_charge="", utility="WATER")
    meter_services.record_reading(owner, meter, read_on=datetime.date(2026, 1, 31), value=10)
    prospect_services.create_prospect(owner, prop, name="Canary Prospect", phone="0712999002")
    inspection_services.start(owner, lease, ConditionReport.Kind.MOVE_IN)
    document_services.upload(owner, prop, title="Canary deed",
                             file=SimpleUploadedFile("deed.pdf", b"%PDF-1.4 canary", content_type="application/pdf"))
    api_services.create_key(owner, name="Canary key", scopes=["payments"])
    esign_services.create(owner, lease, "AGREEMENT", no_renewal_option=True)


@pytest.fixture
def canary(settings, tmp_path, monkeypatch):
    """Bravo is the other organization; Alpha holds the Canary records at Canary Court and a second property."""
    settings.MEDIA_ROOT = tmp_path
    # The sweep sends thousands of requests as one user; the per-user limits (core.throttle) are tested on their own.
    monkeypatch.setattr(throttle, "PER_MINUTE", 10**9)
    monkeypatch.setattr(throttle, "WRITES_PER_MINUTE", 10**9)
    monkeypatch.setattr(throttle, "COSTLY", [])
    bravo = make_org("Bravo Lettings")
    make_property(bravo.organization, "Bravo House")
    _restart_ids()
    alpha = make_org("Alpha Homes")
    court = make_property(alpha.organization, "Canary Court")
    _seed(alpha, court)
    other = make_property(alpha.organization, "Open House")
    _give_first_rows_the_id()
    limited = add_member(alpha.organization, "manager", properties=[other])
    return {"bravo": bravo, "alpha": alpha, "limited": limited}


def _alpha_rows(org):
    counts = {}
    for model in apps.get_models():
        if any(f.name == "organization" for f in model._meta.get_fields()) and not model._meta.proxy:
            counts[model.__name__] = model._base_manager.filter(organization=org).count()
    return counts


def _sweep(client, user, requests, *, other_org=True):
    """Problems found: shown records, server errors, and an id that was not refused."""
    problems = []
    for path, name, _view in routes():
        for method, data in requests:
            if user is not None:
                client.force_login(user)  # a post to /logout/ ends the session
            try:
                response = getattr(client, method)(path, data)
            except Exception as exc:  # noqa: BLE001 - the test client re-raises view errors
                problems.append(f"{method} {path} ({name}): {exc!r}"[:200])
                continue
            shown = (response.status_code == 200 and not response.streaming
                     and MARKER in response.content.decode(errors="ignore"))
            if shown:
                problems.append(f"{method} {path} ({name}): shows a Canary record")
            elif response.status_code >= 500:
                problems.append(f"{method} {path} ({name}): {response.status_code}")
            elif (other_org and str(ID) in path and name not in KEYED_BY_RANDOM_ID
                  and response.status_code not in (401, 403, 404, 405)):
                problems.append(f"{method} {path} ({name}): {response.status_code} for another's id")
    return problems


def test_visitors_reach_only_the_public_pages(client):
    exposed = set()
    for path, name, _view in routes():
        for method in ("get", "post"):
            response = getattr(client, method)(path)
            to_login = response.status_code == 302 and "/login/" in response.get("Location", "")
            if not to_login and not ((name.startswith("api:") or ":web_" in name) and response.status_code in (401, 403, 405)):
                exposed.add(name)
    assert exposed - PUBLIC == set()


def test_another_organization_sees_and_changes_nothing(client, canary):
    alpha = canary["alpha"].organization
    before, events = _alpha_rows(alpha), AuditEvent.objects.filter(organization=alpha).count()
    problems = _sweep(client, canary["bravo"].user, [
        ("get", {}),
        ("post", {}),
        ("post", dict.fromkeys(ID_FIELDS, "not-an-id")),
        ("post", dict.fromkeys(ID_FIELDS, str(ID))),
    ])
    assert problems == []
    assert _alpha_rows(alpha) == before
    assert AuditEvent.objects.filter(organization=alpha).count() == events


def test_staff_kept_to_one_property_see_nothing_of_another(client, canary):
    problems = _sweep(client, canary["limited"].user, [("get", {})], other_org=False)
    assert problems == [], "; ".join(problems)


def test_a_posted_id_that_is_not_an_id_is_simply_not_found(client):
    owner = make_org()
    client.force_login(owner.user)
    response = client.post("/reports/owner-statement/remit/", {"owner": "not-an-id", "month": "2026-01"})
    assert response.status_code == 404


def test_the_production_settings_pass_the_deployment_check():
    """`manage.py check --deploy` with the production settings and made-up values, in its own process."""
    import os
    import secrets
    import subprocess
    import sys

    from cryptography.fernet import Fernet
    from django.conf import settings

    secret = secrets.token_urlsafe(60)
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.settings.prod", "SECRET_KEY": secret,
           "DJANGO_SECRET_KEY": secret, "ALLOWED_HOSTS": "app.example.co.ke", "SITE_URL": "https://app.example.co.ke",
           "REDIS_URL": "redis://localhost:6379/0", "FIELD_ENCRYPTION_KEYS": Fernet.generate_key().decode(),
           "SMS_BACKEND": "notifications.providers.sms.AfricasTalkingSmsSender", "AT_USERNAME": "x", "AT_API_KEY": "x",
           "AT_CALLBACK_TOKEN": secret, "PUSH_BACKEND": "notifications.push.WebPushSender",
           "ASSISTANT_BACKEND": "ai_assistant.client.OpenAIClient", "EMAIL_HOST": "smtp.example.com",
           "DEFAULT_FROM_EMAIL": "rent@example.com", "SENTRY_DSN": "", "WHATSAPP_BACKEND": "",
           # Empty values win over the developer's .env, so the check sees only what is set here.
           "PLATFORM_DARAJA_CONSUMER_KEY": "", "PLATFORM_PAYSTACK_SECRET_KEY": ""}
    result = subprocess.run([sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"], env=env,
                            cwd=settings.BASE_DIR, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
