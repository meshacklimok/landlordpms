"""One organization's data as a ZIP (D-177): asked for on Settings or in the console, made by run_data_exports."""

import datetime
import io
import json
import os
import zipfile

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from django.utils import timezone

from accounts import data_export
from accounts.models import DataExport
from accounts.tests.factories import add_member, make_org, make_property, make_user
from accounts.tests.test_isolation import login
from api import services as api_services
from audit.models import AuditEvent
from console.models import PlatformStaff
from console.tests.test_console import owner as console_owner
from console.tests.test_console import seat_for, signed_in

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.DATA_EXPORT_DIR = tmp_path / "exports"


def _zip(response) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))


def _setup():
    mine = make_org("Kamau Homes")
    make_property(mine.organization, "Kamau Court")
    mine.organization.logo.save("logo.png", ContentFile(b"\x89PNG kamau"))
    api_services.create_key(mine, name="Sync", scopes=["payments"])
    other = make_org("Other Homes")
    make_property(other.organization, "Other Court")
    return mine, other


def test_the_zip_holds_one_organization_its_files_and_no_secrets():
    mine, _other = _setup()
    target, manifest = data_export.build(mine.organization)
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
        properties = zf.read("data/properties/property.csv").decode("utf-8-sig")
        keys = zf.read("data/api/apikey.csv").decode("utf-8-sig")
        users = zf.read("data/accounts/user.csv").decode("utf-8-sig")
        assert zf.read(f"files/{mine.organization.logo.name}") == b"\x89PNG kamau"
        assert json.loads(zf.read("manifest.json"))["tables"]["properties.property"] == 1
    assert "Kamau Court" in properties and "Other Court" not in properties
    assert "key_hash" not in keys and "Sync" in keys
    assert mine.user.phone in users and "password" not in users
    assert "README.txt" in names and "data/accounts/organization.csv" in names
    assert not any("totpdevice" in n or "otpcode" in n for n in names)
    assert manifest["files"] == 1 and manifest["missing_files"] == []


def _make_all():
    return data_export.run_due()


def test_the_owner_asks_the_job_makes_it_and_only_they_download_it(client, outbox, django_capture_on_commit_callbacks):
    mine, _other = _setup()
    partner = add_member(mine.organization, "owner", all_properties=True)
    login(client, mine)
    assert "Download all my data" in client.get(reverse("accounts:organization")).content.decode()
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(reverse("accounts:organization_export")).status_code == 302
        client.post(reverse("accounts:organization_export"))  # a second click waits for the same one
    export = DataExport.objects.get()
    assert export.status == DataExport.Status.PENDING and not export.file
    assert AuditEvent.objects.filter(action="organization.export_data", organization=mine.organization).count() == 1
    assert any("downloaded all the organization's data" in body for to, body in outbox if to == partner.user.phone)

    assert _make_all()["made"] == 1
    export.refresh_from_db()
    assert export.status == DataExport.Status.READY and export.size > 0 and export.rows > 0
    assert any("is ready to download" in body and "/organization/" in body
               for to, body in outbox if to == mine.user.phone)
    url = reverse("accounts:organization_export_download", args=[export.public_id])
    assert url in client.get(reverse("accounts:organization")).content.decode()
    response = client.get(url)
    assert response.status_code == 200 and response["Content-Type"] == "application/zip"
    assert "kamau-homes-data-" in response["Content-Disposition"]
    assert "data/properties/property.csv" in _zip(response).namelist()
    assert AuditEvent.objects.filter(action="data_export.download", organization=mine.organization).exists()

    login(client, partner)  # another owner of the same organization did not ask for it
    assert client.get(url).status_code == 404


def test_the_file_is_deleted_when_it_expires():
    mine, _other = _setup()
    export, _ = data_export.request_export(mine.organization, mine.user, None)
    data_export.make(export)
    path = export.file.path
    export.expires_at = timezone.now() - datetime.timedelta(minutes=1)
    export.save()
    assert _make_all()["expired"] == 1
    export.refresh_from_db()
    assert export.status == DataExport.Status.EXPIRED and not export.can_download
    assert not os.path.exists(path)


def test_a_failure_is_kept_and_a_stuck_run_is_started_again(monkeypatch):
    mine, _other = _setup()
    export, _ = data_export.request_export(mine.organization, mine.user, None)
    monkeypatch.setattr(data_export, "build", lambda org: (_ for _ in ()).throw(RuntimeError("disk full")))
    assert _make_all()["failed"] == 1
    export.refresh_from_db()
    assert export.status == DataExport.Status.FAILED and "disk full" in export.error
    stuck, _ = data_export.request_export(mine.organization, mine.user, None)
    DataExport.objects.filter(pk=stuck.pk).update(status=DataExport.Status.RUNNING,
                                                   started_at=timezone.now() - datetime.timedelta(hours=3))
    monkeypatch.undo()
    assert _make_all()["restarted"] == 1
    stuck.refresh_from_db()
    assert stuck.status == DataExport.Status.READY


def test_staff_without_the_capability_cannot_ask_or_download(client):
    mine, _other = _setup()
    export, _ = data_export.request_export(mine.organization, mine.user, None)
    data_export.make(export)
    login(client, add_member(mine.organization, "manager", all_properties=True))
    assert client.post(reverse("accounts:organization_export")).status_code == 403
    assert client.get(reverse("accounts:organization_export_download", args=[export.public_id])).status_code == 403
    assert DataExport.objects.count() == 1


def test_the_console_owner_makes_one_for_any_organization_and_it_shows_on_its_log(client, outbox):
    mine, _other = _setup()
    console_owner(client)
    org_page = reverse("console:organization", args=[mine.organization.public_id])
    assert reverse("console:organization_export", args=[mine.organization.public_id]) in client.get(org_page).content.decode()
    assert client.post(reverse("console:organization_export", args=[mine.organization.public_id])).status_code == 302
    assert AuditEvent.objects.filter(action="console.organization_export", organization=mine.organization).exists()
    _make_all()
    export = DataExport.objects.get(for_console=True)
    url = reverse("console:organization_export_download", args=[mine.organization.public_id, export.public_id])
    assert url in client.get(org_page).content.decode()
    response = client.get(url)
    assert "Kamau Court" in _zip(response).read("data/properties/property.csv").decode("utf-8-sig")
    # The landlord cannot download the console's copy from their Settings page.
    login(client, mine)
    assert client.get(reverse("accounts:organization_export_download", args=[export.public_id])).status_code == 404


def test_console_seats_that_are_not_superusers_cannot_ask(client):
    mine, _other = _setup()
    signed_in(client, seat_for(role=PlatformStaff.Role.OWNER).user)
    assert client.post(reverse("console:organization_export", args=[mine.organization.public_id])).status_code == 403
    signed_in(client, make_user())
    assert client.post(reverse("console:organization_export", args=[mine.organization.public_id])).status_code == 404
    assert not DataExport.objects.exists()
