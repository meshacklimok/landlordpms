"""The ER diagram in docs/erd.md is generated from the models (D-103)."""

from django.core.management import call_command

from core.management.commands import erd


def test_the_diagram_is_current_with_the_models():
    call_command("erd", "--check")


def test_the_diagram_shows_the_main_links_and_leaves_out_the_organization():
    text = erd.render()
    assert 'LEASE_TENANT }o--|| LEASE : "lease"' in text
    assert 'PAYMENT_ALLOCATION }o--|| INVOICE : "invoice"' in text
    assert ': "organization"' not in text
    assert ': "created_by"' not in text
