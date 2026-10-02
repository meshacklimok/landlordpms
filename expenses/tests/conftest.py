import datetime
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from accounts.tests.factories import make_org, make_property
from expenses import services

D = datetime.date
PAID = D(2025, 1, 15)


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
def repairs(org):
    return services.categories(org).get(name="Repairs and maintenance")


def jpeg(name="r.jpg") -> SimpleUploadedFile:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, "JPEG")
    return SimpleUploadedFile(name, out.getvalue(), content_type="image/jpeg")


def pdf(name="r.pdf", size=None) -> SimpleUploadedFile:
    body = b"%PDF-1.4\n%fake receipt\n"
    if size:
        body += b"0" * (size - len(body))
    return SimpleUploadedFile(name, body, content_type="application/pdf")


def spend(actor, prop, category, amount="2500", *, paid_on=PAID, reference="", **kw):
    kw.setdefault("description", "Fixed the gate lock")
    kw.setdefault("method", "MPESA")
    return services.record_expense(actor, prop, category=category, amount=amount, paid_on=paid_on,
                                   reference=reference, **kw)
