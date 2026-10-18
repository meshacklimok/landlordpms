"""Signing from the tenant portal (D-171): no link or SMS code, the signed-in tenant types their password."""

import pytest
from django.core.exceptions import PermissionDenied
from django.test import Client

from accounts.tests.factories import make_org, make_property, make_user
from billing.tests.test_invoicing import make_lease
from esign import services
from esign.models import Signer, SigningRequest
from portal import services as portal_services

pytestmark = pytest.mark.django_db

Status = SigningRequest.Status


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def lease(owner):
    return make_lease(owner, make_property(owner.organization))


def join(owner, lease, password="s3cret-Passw0rd!"):
    tenant = lease.lease_tenants.get().tenant
    _inv, token = portal_services.invite_tenant(owner, tenant)
    user = make_user(phone=tenant.phone)
    user.set_password(password)
    user.save()
    portal_services.accept_invitation(token, user)
    client = Client()
    client.force_login(user)
    return user, client


def sent(owner, lease):
    req = services.create(owner, lease, "AGREEMENT", no_renewal_option=True)
    return req


def url(lease, req, tail=""):
    return f"/my/leases/{lease.public_id}/sign/{req.public_id}/{tail}"


def test_tenant_signs_in_the_portal_without_a_code(owner, lease):
    user, client = join(owner, lease)
    req = sent(owner, lease)
    assert client.get(f"/my/leases/{lease.public_id}/").context["signings"][0][0] == req
    assert client.get(url(lease, req)).status_code == 200
    assert client.get(url(lease, req, "pdf/")).status_code == 200
    client.post(url(lease, req), {"action": "sign", "typed_name": "Tenant A1", "password": "s3cret-Passw0rd!",
                                  "agree": "on"})
    signer = req.signers.get(role=Signer.Role.TENANT)
    assert signer.has_signed and signer.signed_via == Signer.Via.PORTAL and signer.verified_at


def test_wrong_password_does_not_sign(owner, lease):
    _user, client = join(owner, lease)
    req = sent(owner, lease)
    client.post(url(lease, req), {"action": "sign", "typed_name": "Tenant A1", "password": "nope", "agree": "on"})
    assert not req.signers.get(role=Signer.Role.TENANT).has_signed


def test_both_signed_completes_the_request(owner, lease):
    _user, client = join(owner, lease)
    req = sent(owner, lease)
    services.sign_as_landlord(owner, req, typed_name="Landlord", agreed=True)
    client.post(url(lease, req), {"action": "sign", "typed_name": "Tenant A1", "password": "s3cret-Passw0rd!",
                                  "agree": "on"})
    req.refresh_from_db()
    assert req.status == Status.COMPLETED and req.signed_pdf
    assert client.get(url(lease, req, "pdf/")).status_code == 200


def test_decline_in_the_portal_closes_the_request(owner, lease):
    _user, client = join(owner, lease)
    req = sent(owner, lease)
    client.post(url(lease, req), {"action": "decline", "reason": "Rent is wrong"})
    req.refresh_from_db()
    assert req.status == Status.DECLINED


def test_another_tenant_cannot_open_or_sign(owner, lease):
    other = make_lease(owner, lease.unit.property, code="B7")
    _user, stranger = join(owner, other)
    req = sent(owner, lease)
    assert stranger.get(url(lease, req)).status_code == 404
    assert stranger.get(url(other, req)).status_code == 404
    with pytest.raises(PermissionDenied):
        services.sign_in_portal(_user, req, typed_name="X Y", agreed=True)


def test_a_different_phone_on_the_account_cannot_sign(owner, lease):
    user, client = join(owner, lease)
    user.phone = "+254799000111"
    user.save(update_fields=["phone"])
    req = sent(owner, lease)
    assert client.get(url(lease, req)).status_code == 404
