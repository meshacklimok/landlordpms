from accounts.models import Organization
from notifications import catalog, emails


def test_rent_reminder_subject_uses_the_billers_word():
    ntype = catalog.get("rent_due_soon")
    estate = Organization(org_type=Organization.Type.ESTATE)
    landlord = Organization(org_type=Organization.Type.INDIVIDUAL)
    assert emails.subject(estate, ntype, "en").startswith("Service charge reminder")
    assert emails.subject(landlord, ntype, "en").startswith("Rent reminder")


def test_subjects_without_rent_and_swahili_are_untouched():
    water = Organization(org_type=Organization.Type.WATER)
    assert emails.subject(water, catalog.get("invoice_issued"), "en") == catalog.get("invoice_issued").subject("en")
    ntype = catalog.get("rent_overdue")
    assert emails.subject(water, ntype, "sw") == ntype.subject("sw")
