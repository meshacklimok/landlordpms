import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from accounts.tests.factories import make_org, make_property


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


def jpeg(size=(40, 30), fmt="JPEG", mode="RGB", exif=None, name="p.jpg") -> SimpleUploadedFile:
    out = io.BytesIO()
    img = Image.new(mode, size, "red" if mode == "RGB" else (255, 0, 0, 128))
    kw = {"exif": exif} if exif is not None else {}
    img.save(out, fmt, **kw)
    return SimpleUploadedFile(name, out.getvalue(), content_type=f"image/{fmt.lower()}")
