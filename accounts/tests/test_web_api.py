"""The JSON the Next.js screens read (D-168): the member's own session, the same figures as the Django page."""

import re
from pathlib import Path

import pytest
from django.urls import reverse

from .factories import add_member, make_org
from .test_isolation import login

pytestmark = pytest.mark.django_db


TYPES = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "types.ts"


def _declared_keys(type_name: str) -> set[str]:
    """The top-level field names `export type <type_name> = { ... }` declares in web/src/lib/types.ts."""
    source = TYPES.read_text(encoding="utf-8")
    body = source.split(f"export type {type_name} = {{", 1)[1]
    keys, depth = set(), 1
    for line in body.splitlines():
        if depth == 1:
            m = re.match(r"^  (\w+)\??:", line)
            if m:
                keys.add(m.group(1))
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            break
    return keys


def test_the_json_has_the_fields_the_screens_declare(client):
    """A key added to or dropped from web_api.py without types.ts (or the other way) fails here, not in a browser."""
    owner = make_org()
    login(client, owner)
    assert set(client.get(reverse("accounts:web_home")).json()) == _declared_keys("Home")
    assert set(client.get(reverse("accounts:web_session")).json()) == _declared_keys("Session")


def test_a_visitor_gets_401_not_a_redirect(client):
    for name in ("accounts:web_home", "accounts:web_session"):
        response = client.get(reverse(name))
        assert response.status_code == 401
        assert response.json()["error"] == "signed_out"


def test_the_home_data_matches_the_page(client):
    owner = make_org()
    login(client, owner)
    data = client.get(reverse("accounts:web_home")).json()
    assert data["organization"] == owner.organization.name
    assert data["checklist"]["total"] == 6 and data["checklist"]["done"] == 0
    assert data["figures"] is None  # nothing billed or paid yet: setup steps, not rows of zeros
    assert [a["key"] for a in data["actions"]][0] == "record"
    assert data["actions"][0]["main"] is True


def test_links_follow_the_members_capabilities(client):
    owner = make_org()
    add_member(owner.organization, "caretaker")
    login(client, owner)
    owner_keys = {a["key"] for a in client.get(reverse("accounts:web_home")).json()["actions"]}
    assert "invoices" in owner_keys


def test_the_session_names_the_member(client):
    owner = make_org()
    login(client, owner)
    data = client.get(reverse("accounts:web_session")).json()
    assert data["organization"]["name"] == owner.organization.name
    assert data["logout_url"] == reverse("accounts:logout")
