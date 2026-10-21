import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from accounts.tests.factories import make_org, make_property, make_user
from documents import services
from portal import services as portal_services
from reports.tests.test_income import make_lease


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


def pdf(name="lease.pdf") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"%PDF-1.4\n%fake\n", content_type="application/pdf")


def jpeg(name="id.jpg", fmt="JPEG") -> SimpleUploadedFile:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, fmt)
    return SimpleUploadedFile(name, out.getvalue())


def office(name="rules.docx", folder="word/") -> SimpleUploadedFile:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(f"{folder}document.xml", "<doc/>")
    return SimpleUploadedFile(name, out.getvalue())


def upload(actor, place, **kw):
    kw.setdefault("file", pdf())
    return services.upload(actor, place, **kw)


def portal_user(owner, lease):
    """Invites the lease's tenant to the portal and accepts as a verified user on their phone."""
    tenant = lease.lease_tenants.get().tenant
    _invitation, token = portal_services.invite_tenant(owner, tenant)
    user = make_user(phone=tenant.phone)
    portal_services.accept_invitation(token, user)
    return user
