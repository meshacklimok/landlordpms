import datetime
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image

from accounts.tests.factories import make_org, make_property, make_user
from expenses.models import Supplier
from maintenance import services
from portal import services as portal_services
from reports.tests.test_income import make_lease

NOW = timezone.make_aware(datetime.datetime(2026, 3, 2, 9, 0))


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def org(owner):
    return owner.organization


@pytest.fixture
def prop(org):
    return make_property(org, name="Acacia Court")


@pytest.fixture
def lease(owner, prop):
    """Tenant A1 on an active lease of unit A1."""
    return make_lease(owner, prop, code="A1")


@pytest.fixture
def unit(lease):
    return lease.unit


@pytest.fixture
def tenant(lease):
    return lease.lease_tenants.get().tenant


@pytest.fixture
def fundi(org, owner):
    return Supplier.objects.create(organization=org, name="Juma Plumbing", phone="0722000111", created_by=owner.user)


def jpeg(name="p.jpg") -> SimpleUploadedFile:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, "JPEG")
    return SimpleUploadedFile(name, out.getvalue(), content_type="image/jpeg")


def report(actor, prop, title="Kitchen sink is leaking", **kw):
    kw.setdefault("now", NOW)
    return services.report(actor, prop, title=title, **kw)


def portal_user(owner, lease):
    """Invites the lease's tenant to the portal and accepts as a verified user on their phone."""
    tenant = lease.lease_tenants.get().tenant
    _invitation, token = portal_services.invite_tenant(owner, tenant)
    user = make_user(phone=tenant.phone)
    portal_services.accept_invitation(token, user)
    return user
