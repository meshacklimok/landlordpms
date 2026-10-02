import pytest
from django.db import transaction

from accounts.tests.factories import make_org
from core.models import NumberSequence
from core.numbering import next_number

pytestmark = pytest.mark.django_db


def test_numbers_count_up_per_org_key_and_period():
    a, b = make_org().organization, make_org().organization
    with transaction.atomic():
        assert next_number(a, "lease", prefix="LSE", period="2026") == "LSE-2026-000001"
        assert next_number(a, "lease", prefix="LSE", period="2026") == "LSE-2026-000002"
        assert next_number(a, "lease", prefix="LSE", period="2027") == "LSE-2027-000001"
        assert next_number(b, "lease", prefix="LSE", period="2026") == "LSE-2026-000001"
        assert next_number(a, "receipt", prefix="RCT") == "RCT-000001"
    assert NumberSequence.objects.get(organization=a, key="lease", period="2026").next_value == 3


def test_rolled_back_issue_gives_the_number_back():
    org = make_org().organization
    with pytest.raises(RuntimeError), transaction.atomic():
        next_number(org, "lease", prefix="LSE", period="2026")
        raise RuntimeError
    with transaction.atomic():
        assert next_number(org, "lease", prefix="LSE", period="2026") == "LSE-2026-000001"


@pytest.mark.django_db(transaction=True)
def test_needs_a_transaction():
    with pytest.raises(RuntimeError):
        next_number(make_org().organization, "lease", prefix="LSE")
