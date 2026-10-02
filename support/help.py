"""Help topics (D-063 item 2). Each is a template under templates/support/help/, reviewed like code."""

from django.utils.translation import gettext_lazy as _

TOPICS = [
    ("getting-started", _("Getting started"), _("Set up your workspace, first property and first invoice.")),
    ("properties-units", _("Properties and units"), _("Add properties, buildings and units, and their rent.")),
    ("tenants-leases", _("Tenants and leases"), _("Add tenants, start and end leases, deposits.")),
    ("invoices-payments", _("Invoices and payments"), _("Monthly bills, recording payments, receipts, arrears.")),
    ("mpesa", _("M-Pesa"), _("Connect your Paybill or Till and match payments.")),
    ("messages", _("Messages and SMS credit"), _("Reminders, announcements, quiet hours and SMS credit.")),
    ("water", _("Water meters"), _("Meters, readings, approval and water charges.")),
    ("staff-roles", _("Staff and roles"), _("Invite your team and choose what each person can do.")),
    ("subscription", _("Subscription and billing"), _("Plans, limits, paying us and receipts.")),
    ("importing", _("Importing your data"), _("Bring units, tenants and balances in from a spreadsheet.")),
]
SLUGS = {slug for slug, _t, _d in TOPICS}


def topic(slug: str):
    return next(((s, t, d) for s, t, d in TOPICS if s == slug), None)
