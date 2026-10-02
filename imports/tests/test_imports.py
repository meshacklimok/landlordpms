"""CSV import: preview changes nothing, apply keeps the good rows, undo removes unused records (doc 14 B8)."""

import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from accounts.tests.factories import add_member, fresh, make_org
from accounts.tests.test_isolation import login
from audit.models import AuditEvent
from imports import services
from imports.models import ImportBatch
from leases import services as lease_services
from properties import services as property_services
from properties.models import Unit
from tenants.models import Tenant

pytestmark = pytest.mark.django_db

UNITS, TENANTS = ImportBatch.Kind.UNITS, ImportBatch.Kind.TENANTS


def csv_file(text: str, name="data.csv", encoding="utf-8"):
    return SimpleUploadedFile(name, text.encode(encoding), content_type="text/csv")


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def prop(owner):
    prop = property_services.create_property(owner, name="Greenview", code="GV")
    property_services.create_building(owner, prop, name="Block A")
    return prop


UNIT_CSV = """Property,Unit,Building,Type,Label,Rent
gv,a1,block a,apartment,2 bedroom,"15,000"
GV,A2,,Bedsitter,,KES 8000
GV,a1,,,,
XX,B1,,,,
GV,B2,Block Z,,,
GV,B3,,castle,,
GV,B4,,,,lots
,B5,,,,

"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_normalises_headers_and_skips_blank_rows():
    rows = services.parse_csv(UNITS, csv_file("﻿Property Code, Unit Code \nGV,A1\n,,\nGV,A2\n"))
    assert [r["data"] for r in rows] == [{"property_code": "GV", "unit_code": "A1"},
                                         {"property_code": "GV", "unit_code": "A2"}]
    assert [r["line"] for r in rows] == [2, 4]


@pytest.mark.parametrize("text, message", [
    ("", "empty"),
    ("property_code,unit_code,colour\nGV,A1,red\n", "Unknown columns: colour"),
    ("unit_code\nA1\n", "Missing columns: property_code"),
    ("property_code,unit_code\n", "no rows"),
])
def test_parse_rejects_bad_files(text, message):
    with pytest.raises(ValidationError, match=message):
        services.parse_csv(UNITS, csv_file(text))


def test_parse_limits(monkeypatch):
    monkeypatch.setattr(services, "MAX_ROWS", 2)
    with pytest.raises(ValidationError, match="at most 2 rows"):
        services.parse_csv(UNITS, csv_file("property_code,unit_code\nGV,A1\nGV,A2\nGV,A3\n"))
    monkeypatch.setattr(services, "MAX_BYTES", 10)
    with pytest.raises(ValidationError, match="too big"):
        services.parse_csv(UNITS, csv_file("property_code,unit_code\nGV,A1\n"))


def test_parse_reads_excel_encoding():
    rows = services.parse_csv(TENANTS, csv_file("name,phone\nJosé,0712345678\n", encoding="cp1252"))
    assert rows[0]["data"]["name"] == "José"


def test_template_has_every_column():
    header = services.template_csv(TENANTS).splitlines()[0]
    assert header.split(",") == list(services.COLUMNS[TENANTS])


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


def test_unit_preview_changes_nothing(owner, prop):
    batch = services.preview_import(owner, UNITS, csv_file(UNIT_CSV))
    assert Unit.objects.filter(property=prop).count() == 0
    assert batch.status == ImportBatch.Status.PREVIEW
    assert (batch.ok_count, batch.error_count) == (2, 6)
    errors = [r["errors"] for r in batch.rows]
    assert errors[0] == [] and errors[1] == []
    assert [r["label"] for r in batch.rows[:2]] == ["GV-A1", "GV-A2"]
    assert "already has a unit A1" in errors[2][0]  # duplicate within the file
    assert "No property with code XX" in errors[3][0]
    assert "no building called" in errors[4][0]
    assert "not a valid choice" in errors[5][0]
    assert "Enter an amount" in errors[6][0]
    assert errors[7] == ["property_code is required."]


def test_unit_apply_and_undo(owner, prop):
    batch = services.apply_import(owner, services.preview_import(owner, UNITS, csv_file(UNIT_CSV)))
    assert batch.status == ImportBatch.Status.APPLIED and batch.ok_count == 2
    a1 = Unit.objects.get(property=prop, code="A1")
    assert a1.building.name == "Block A" and a1.type_label == "2 bedroom" and a1.list_rent == Decimal("15000")
    assert Unit.objects.get(property=prop, code="A2").unit_type == Unit.Type.BEDSITTER
    assert AuditEvent.objects.filter(action="import.apply", object_id=str(batch.public_id)).exists()
    assert AuditEvent.objects.filter(action="unit.create").count() == 2
    with pytest.raises(ValidationError):
        services.apply_import(owner, batch)

    # A unit already let is kept; the rest go.
    tenant = Tenant.objects.create(organization=owner.organization, name="W", phone="+254712345678")
    lease_services.create_lease(owner, unit=a1, tenants=[tenant], start_date=datetime.date.today(),
                                end_date=None, rent=15000)
    removed, kept = services.undo_import(owner, batch)
    assert removed == ["GV-A2"] and kept == ["GV-A1"]
    assert list(Unit.objects.filter(property=prop).values_list("code", flat=True)) == ["A1"]
    batch.refresh_from_db()
    assert batch.status == ImportBatch.Status.UNDONE
    with pytest.raises(ValidationError):
        services.undo_import(owner, batch)


def test_undo_window_closes(owner, prop):
    batch = services.apply_import(owner, services.preview_import(owner, UNITS, csv_file(UNIT_CSV)))
    ImportBatch.objects.filter(pk=batch.pk).update(applied_at=timezone.now() - datetime.timedelta(hours=25))
    batch.refresh_from_db()
    with pytest.raises(ValidationError, match="24 hours"):
        services.undo_import(owner, batch)


def test_unit_import_respects_scope(owner, prop):
    elsewhere = property_services.create_property(owner, name="Riverside", code="RS")
    manager = fresh(add_member(owner.organization, "manager", properties=[prop]))
    batch = services.preview_import(manager, UNITS, csv_file("property_code,unit_code\nGV,C1\nRS,C1\n"))
    assert [r["errors"] for r in batch.rows] == [[], ["No property with code RS."]]
    assert elsewhere.units.count() == 0

    agent = fresh(add_member(owner.organization, "leasing_agent", properties=[prop]))
    with pytest.raises(PermissionDenied):
        services.preview_import(agent, UNITS, csv_file("property_code,unit_code\nGV,C1\n"))
    with pytest.raises(PermissionDenied):
        services.apply_import(make_org(), batch)


def test_discard(owner, prop):
    batch = services.discard_import(owner, services.preview_import(owner, UNITS, csv_file(UNIT_CSV)))
    assert batch.status == ImportBatch.Status.DISCARDED and batch.rows == []
    with pytest.raises(ValidationError):
        services.apply_import(owner, batch)


# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------


TENANT_CSV = """name,phone,email,kind,id_type,id_number,kra_pin
Wanjiku Kamau,0712 345 678,w@example.com,,national id,12345678,a123456789b
Otieno Ltd,+254722000111,,company,,,
Copy,0712345678,,,,,
Bad Phone,12,,,,,
"""


def test_tenant_import(owner):
    batch = services.preview_import(owner, TENANTS, csv_file(TENANT_CSV))
    assert Tenant.objects.count() == 0
    assert [bool(r["errors"]) for r in batch.rows] == [False, False, True, True]
    assert "already exists" in batch.rows[2]["errors"][0]

    batch = services.apply_import(owner, batch)
    w = Tenant.objects.get(name="Wanjiku Kamau")
    assert w.phone == "+254712345678" and w.id_type == Tenant.IdType.NATIONAL_ID and w.kra_pin == "A123456789B"
    assert Tenant.objects.get(name="Otieno Ltd").kind == Tenant.Kind.COMPANY
    # ID numbers are not kept in the batch once applied.
    assert "id_number" not in batch.rows[0]["data"]

    removed, kept = services.undo_import(owner, batch)
    assert sorted(removed) == ["Otieno Ltd", "Wanjiku Kamau"] and kept == []
    assert Tenant.all_objects.count() == 0


def test_tenant_import_needs_sensitive_access_for_ids(owner, prop):
    agent = fresh(add_member(owner.organization, "leasing_agent", properties=[prop]))
    batch = services.preview_import(agent, TENANTS, csv_file(TENANT_CSV))
    assert "ID numbers" in batch.rows[0]["errors"][0]
    assert batch.rows[1]["errors"] == []


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def test_import_pages(client, owner, prop):
    login(client, owner)
    assert client.get(reverse("imports:list")).status_code == 200
    r = client.get(reverse("imports:template", args=["units"]))
    assert r.status_code == 200 and r["Content-Type"] == "text/csv"
    assert client.get(reverse("imports:template", args=["nope"])).status_code == 404

    r = client.post(reverse("imports:upload", args=["units"]), {"file": csv_file(UNIT_CSV)})
    batch = ImportBatch.objects.get()
    assert r["Location"] == reverse("imports:detail", args=[batch.public_id])
    detail = reverse("imports:detail", args=[batch.public_id])
    assert len(client.get(detail).context["page"]) == 8
    assert len(client.get(detail, {"errors": "1"}).context["page"]) == 6

    client.post(detail, {"action": "apply"})
    assert Unit.objects.filter(property=prop).count() == 2
    client.post(detail, {"action": "undo"})
    assert Unit.objects.filter(property=prop).count() == 0

    r = client.post(reverse("imports:upload", args=["units"]), {"file": csv_file("colour\nred\n")}, follow=True)
    assert "Unknown columns" in r.content.decode()


def test_import_pages_are_gated(client, owner, prop):
    other = make_org()
    batch = services.preview_import(other, TENANTS, csv_file("name,phone\nA,0712345678\n"))
    login(client, owner)
    assert client.get(reverse("imports:detail", args=[batch.public_id])).status_code == 404

    login(client, add_member(owner.organization, "viewer", all_properties=True))
    assert client.get(reverse("imports:list")).status_code == 403
    assert client.post(reverse("imports:upload", args=["tenants"]),
                       {"file": csv_file("name,phone\nA,0712345678\n")}).status_code == 403


# ---------------------------------------------------------------------------
# Who may see a batch, and how long a preview lives
# ---------------------------------------------------------------------------


def test_scoped_members_only_see_their_own_batches(client, owner, prop):
    manager = fresh(add_member(owner.organization, "manager", all_properties=True))
    scoped = fresh(add_member(owner.organization, "manager", properties=[prop]))
    theirs = services.preview_import(manager, TENANTS, csv_file(TENANT_CSV))
    mine = services.preview_import(scoped, TENANTS, csv_file("name,phone\nA,0733000111\n"))

    assert list(services.visible_batches(scoped)) == [mine]
    assert set(services.visible_batches(owner)) == {theirs, mine}
    for action in (services.apply_import, services.discard_import):
        with pytest.raises(PermissionDenied):
            action(scoped, theirs)
    applied = services.apply_import(manager, theirs)
    with pytest.raises(PermissionDenied):
        services.undo_import(scoped, applied)

    login(client, scoped)
    assert client.get(reverse("imports:detail", args=[theirs.public_id])).status_code == 404
    assert client.post(reverse("imports:detail", args=[theirs.public_id]), {"action": "undo"}).status_code == 404
    assert Tenant.objects.filter(name="Wanjiku Kamau").exists()
    assert list(client.get(reverse("imports:list")).context["page"]) == [mine]


def test_stale_previews_expire_and_are_purged(owner):
    batch = services.preview_import(owner, TENANTS, csv_file(TENANT_CSV))
    ImportBatch.objects.filter(pk=batch.pk).update(created_at=timezone.now() - datetime.timedelta(hours=25))
    batch.refresh_from_db()
    with pytest.raises(ValidationError, match="more than a day old"):
        services.apply_import(owner, batch)
    fresh_batch = services.preview_import(owner, TENANTS, csv_file(TENANT_CSV))

    assert services.purge_stale_previews() == 1
    batch.refresh_from_db()
    assert batch.status == ImportBatch.Status.DISCARDED and batch.rows == []
    fresh_batch.refresh_from_db()
    assert fresh_batch.status == ImportBatch.Status.PREVIEW and fresh_batch.rows
