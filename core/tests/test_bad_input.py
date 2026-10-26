"""Pages that failed with a server error on junk in the address (found in the 2026-10-24 audit)."""

import pytest
from django.urls import reverse

from accounts.tests.factories import make_org, make_property
from accounts.tests.test_isolation import login
from meters.tests.conftest import let, make_meter

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return make_org()


@pytest.fixture
def signed_in(client, owner):
    login(client, owner)
    return client


def test_a_nul_byte_in_a_search_is_a_400(signed_in):
    assert signed_in.get("/billing/invoices/" + "?q=%00").status_code == 400
    assert signed_in.post("/billing/invoices/", "q=%00", content_type="application/x-www-form-urlencoded"
                          ).status_code == 400


def test_junk_ids_in_the_address_are_not_server_errors(signed_in, owner):
    prop = make_property(owner.organization)
    _lease, unit = let(owner, prop)
    for url in (reverse("reports:tenant_statement") + "?lease=abc",
                reverse("leases:create", args=[unit.public_id]) + "?tenant=abc"):
        assert signed_in.get(url).status_code < 500, url


def test_the_qr_labels_page_opens(signed_in, owner):
    prop = make_property(owner.organization)
    _lease, unit = let(owner, prop)
    make_meter(owner, prop, [unit])
    url = reverse("meters:qr_labels", args=[prop.public_id])
    assert signed_in.get(url + "?utility=WATER").status_code == 200
    assert signed_in.get(url + "?utility=junk").status_code == 200


def test_message_a_tenant_without_a_tenant_lists_tenants(signed_in):
    r = signed_in.get(reverse("notifications:compose"))
    assert r.status_code == 200
    assert "Who do you want to message?" in r.content.decode()
