import datetime
import io
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from accounts.tests.factories import make_org, make_property
from billing.tests.test_invoicing import make_lease
from meters import services

D = datetime.date
JAN1 = D(2026, 1, 1)


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    return make_property(owner.organization)


def jpeg(name="m.jpg") -> SimpleUploadedFile:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "blue").save(out, "JPEG")
    return SimpleUploadedFile(name, out.getvalue(), content_type="image/jpeg")


def make_meter(owner, prop, units, *, rate="100", minimum="", split="EQUAL", kind="POSTPAID", label="W1"):
    """units: Unit objects, or (Unit, weight) pairs."""
    served = [u if isinstance(u, tuple) else (u, "1") for u in units]
    return services.create_meter(owner, prop, units=served, label=label, serial="", kind=kind, split=split,
                                 rate=rate, minimum_charge=minimum)


def read(owner, meter, day, value, **kw):
    return services.record_reading(owner, meter, read_on=day, value=Decimal(str(value)), **kw)


def let(owner, prop, code="A1", start=JAN1, **kw):
    """An active lease on a new unit. Returns (lease, unit)."""
    lease = make_lease(owner, prop, code=code, start=start, **kw)
    return lease, lease.unit
