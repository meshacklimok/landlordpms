"""core/ helpers and the append-only audit log."""

import pytest

from audit import services as audit
from audit.models import AuditEvent
from core import ratelimit
from core.phone import InvalidPhoneNumber, mask_phone, normalize_phone

from .factories import make_org


@pytest.mark.parametrize(
    "raw",
    ["0712345678", "0712 345 678", "712345678", "254712345678", "+254712345678", "+254 712-345-678",
     "00254712345678", "(0712) 345678"],
)
def test_normalize_kenyan_formats(raw):
    assert normalize_phone(raw) == "+254712345678"


def test_normalize_safaricom_01_range():
    assert normalize_phone("0110 123456") == "+254110123456"


def test_normalize_foreign_number_needs_plus():
    assert normalize_phone("+447911123456") == "+447911123456"


@pytest.mark.parametrize("raw", ["", "abc", "0712", "0212345678", "07123456789", "+254612345678", "+12", None])
def test_normalize_rejects(raw):
    with pytest.raises(InvalidPhoneNumber):
        normalize_phone(raw)


def test_mask_phone():
    assert mask_phone("+254712345678") == "+254 7** *** 678"


def test_ratelimit():
    for _ in range(3):
        assert ratelimit.hit("t", 3, 60)
    assert not ratelimit.hit("t", 3, 60)
    ratelimit.reset("t")
    assert ratelimit.hit("t", 3, 60)


@pytest.mark.django_db
def test_audit_log_is_append_only():
    owner = make_org()
    event = audit.record("test.event", actor=owner.user, organization=owner.organization, obj=owner)
    assert event.object_id == str(owner.public_id)
    event.action = "tampered"
    with pytest.raises(PermissionError):
        event.save()
    with pytest.raises(PermissionError):
        event.delete()
    with pytest.raises(PermissionError):
        AuditEvent.objects.filter(pk=event.pk).update(action="tampered")
    with pytest.raises(PermissionError):
        AuditEvent.objects.filter(pk=event.pk).delete()
    assert AuditEvent.objects.get(pk=event.pk).action == "test.event"


@pytest.mark.django_db
def test_audit_for_org_refuses_none():
    with pytest.raises(ValueError):
        AuditEvent.objects.for_org(None)
