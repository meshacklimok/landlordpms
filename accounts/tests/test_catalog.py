import pytest

from accounts import services
from accounts.capabilities import ALL_CODENAMES, OWNER_CRITICAL, ROLE_TEMPLATES
from accounts.models import Capability, Role, RoleTemplate

from .factories import make_org

pytestmark = pytest.mark.django_db


def test_catalog_is_synced_after_migrate():
    active = set(Capability.objects.filter(is_active=True).values_list("codename", flat=True))
    assert active == set(ALL_CODENAMES)
    assert set(RoleTemplate.objects.values_list("key", flat=True)) == {t.key for t in ROLE_TEMPLATES}


def test_sync_is_idempotent():
    services.sync_access_catalog()
    stats = services.sync_access_catalog()
    assert stats == {"created": 0, "updated": 0, "deactivated": 0, "templates_created": 0}


def test_removed_capability_is_deactivated_not_deleted():
    Capability.objects.create(codename="legacy.thing", module="legacy", description="old")
    services.sync_access_catalog()
    assert Capability.objects.get(codename="legacy.thing").is_active is False


def test_owner_critical_is_in_catalog():
    assert OWNER_CRITICAL <= ALL_CODENAMES


@pytest.mark.parametrize("template", ROLE_TEMPLATES, ids=lambda t: t.key)
def test_new_org_role_matches_template(template):
    org = make_org().organization
    r = Role.objects.get(organization=org, based_on_template__key=template.key)
    assert services.role_codenames(r) == set(template.capabilities)
    assert r.is_owner_role is template.is_owner


def test_each_org_gets_its_own_role_copies():
    a, b = make_org().organization, make_org().organization
    assert not set(Role.objects.filter(organization=a).values_list("pk", flat=True)) & set(
        Role.objects.filter(organization=b).values_list("pk", flat=True)
    )
