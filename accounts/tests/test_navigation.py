import pytest
from django.test import RequestFactory
from django.urls import reverse

from accounts.navigation import nav_groups

from .factories import add_member, make_org
from .test_isolation import login

pytestmark = pytest.mark.django_db


def _links(groups):
    return {item["url"] for group in groups for item in group["items"]}


def test_the_owner_sees_every_group_and_the_current_page_is_marked():
    owner = make_org()
    request = RequestFactory().get(reverse("properties:units"))
    groups = nav_groups(request, owner, assistant_set_up=False)
    assert [g["label"] for g in groups] == ["Property", "Tenants", "Money", "More"]
    assert {reverse("properties:list"), reverse("properties:units"), reverse("billing:invoices"),
            reverse("accounts:staff")} <= _links(groups)
    assert reverse("ai_assistant:ask") not in _links(groups)
    active = [(g["label"], i["url"]) for g in groups for i in g["items"] if i["active"]]
    # /properties/units/ is marked, not /properties/ which it starts with.
    assert active == [("Property", reverse("properties:units"))]
    assert [g["label"] for g in groups if g["active"]] == ["Property"]


def test_links_and_empty_groups_follow_the_role():
    owner = make_org()
    caretaker = add_member(owner.organization, "caretaker", all_properties=True)
    groups = nav_groups(RequestFactory().get("/"), caretaker, assistant_set_up=False)
    links = _links(groups)
    assert reverse("accounts:staff") not in links and reverse("accounts:roles") not in links
    assert all(g["items"] for g in groups)
    assert nav_groups(RequestFactory().get("/"), None, assistant_set_up=True) == []


def test_the_top_bar_shows_grouped_menus(client):
    owner = make_org()
    login(client, owner)
    page = client.get(reverse("accounts:home")).content.decode()
    # The groups sit in one sidebar that becomes a drawer on small screens (D-141); only the account menu drops down.
    assert 'id="app-menu"' in page and 'data-bs-target="#app-menu"' in page
    assert page.count('data-bs-toggle="dropdown"') == 1
    assert f'href="{reverse("properties:units")}"' in page
    assert "data-logout" in page and "data-install-app hidden" in page
