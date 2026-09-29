"""Photo handling (D-047 item 4): every upload is re-encoded, shrunk and stripped of EXIF."""

import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from billing.tests.test_invoicing import make_lease
from inspections import photos, services
from inspections.models import ConditionPhoto, ConditionReport

from .conftest import jpeg

pytestmark = pytest.mark.django_db


def test_photos_are_reencoded_without_exif():
    exif = Image.Exif()
    exif[0x8825] = {2: (1.0, 17.0, 0.0)}  # GPS
    exif[0x010F] = "PhoneMaker"
    content, w, h = photos.process(jpeg(exif=exif.tobytes()))
    with Image.open(io.BytesIO(content.read())) as img:
        assert img.format == "JPEG" and (img.width, img.height) == (w, h) == (40, 30)
        assert not img.getexif()
    assert content.name.endswith(".jpg") and len(content.name) == 36


def test_large_photos_are_shrunk_and_png_flattened():
    content, w, h = photos.process(jpeg(size=(3200, 1000), fmt="PNG", mode="RGBA", name="p.png"))
    assert (w, h) == (1600, 500)
    with Image.open(io.BytesIO(content.read())) as img:
        assert img.mode == "RGB"


def test_files_that_are_not_photos_are_refused():
    with pytest.raises(ValidationError):
        photos.process(SimpleUploadedFile("x.jpg", b"<?php echo 1; ?>", content_type="image/jpeg"))


def test_oversize_uploads_are_refused(monkeypatch):
    monkeypatch.setattr(photos, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(ValidationError):
        photos.process(jpeg())


def test_photo_limits_and_removal(owner, prop, monkeypatch, django_capture_on_commit_callbacks):
    lease = make_lease(owner, prop)
    report = services.start(owner, lease, ConditionReport.Kind.MOVE_IN)
    line = report.lines.first()
    monkeypatch.setattr(services, "MAX_PHOTOS_PER_LINE", 1)
    photo = services.add_photo(owner, report, jpeg(), line=line, caption="Door")
    assert photo.image.name.startswith("condition-reports/") and photo.image.storage.exists(photo.image.name)
    with pytest.raises(ValidationError):
        services.add_photo(owner, report, jpeg(), line=line)
    services.add_photo(owner, report, jpeg())  # a general photo still fits
    name = photo.image.name
    with django_capture_on_commit_callbacks(execute=True):
        services.remove_photo(owner, photo)
    assert not ConditionPhoto.objects.filter(pk=photo.pk).exists()
    assert not photo.image.storage.exists(name)


def test_photos_cannot_change_on_a_completed_report(owner, prop):
    lease = make_lease(owner, prop)
    report = services.start(owner, lease, ConditionReport.Kind.MOVE_IN)
    photo = services.add_photo(owner, report, jpeg())
    services.save_details(owner, report, inspected_on=report.inspected_on,
                          lines={ln.pk: ("GOOD", "") for ln in report.lines.all()})
    services.complete(owner, report)
    with pytest.raises(ValidationError):
        services.add_photo(owner, report, jpeg())
    with pytest.raises(ValidationError):
        services.remove_photo(owner, photo)
