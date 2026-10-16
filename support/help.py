"""Help topics (D-063 item 2). Each is a template under templates/support/help/, reviewed like code."""

import html
import re

from django.conf import settings
from django.template import Context
from django.template.loader import get_template
from django.template.loader_tags import BlockNode
from django.utils.translation import gettext_lazy as _

TOPICS = [
    ("getting-started", _("Getting started"), _("Set up your workspace, first property and first invoice.")),
    ("properties-units", _("Properties and units"), _("Add properties, buildings and units, and their rent.")),
    ("tenants-leases", _("Tenants and leases"), _("Add tenants, start and end leases, deposits.")),
    ("invoices-payments", _("Invoices and payments"), _("Monthly bills, recording payments, receipts, arrears.")),
    ("mpesa", _("M-Pesa"), _("Connect your Paybill or Till and match payments.")),
    ("messages", _("Messages and SMS credit"), _("Reminders, announcements, quiet hours and SMS credit.")),
    ("water", _("Water and electricity meters"), _("Meters, readings, approval and metered charges.")),
    ("prospects", _("Prospects and viewings"), _("People asking about a unit, viewings, holding a unit, new tenants.")),
    ("maintenance", _("Maintenance and suppliers"),
     _("Repair requests, assigning staff and suppliers, targets and who keeps to them.")),
    ("documents", _("Documents"), _("Leases, ID copies, title deeds, insurance and sharing with tenants.")),
    ("assistant", _("Ask the assistant"),
     _("Questions about rent, arrears, vacancies and repairs, answered from your figures.")),
    ("staff-roles", _("Staff and roles"), _("Invite your team and choose what each person can do.")),
    ("subscription", _("Subscription and billing"), _("Plans, limits, paying us and receipts.")),
    ("importing", _("Importing your data"), _("Bring units, tenants and balances in from a spreadsheet.")),
]
SLUGS = {slug for slug, _t, _d in TOPICS}


def topic(slug: str):
    return next(((s, t, d) for s, t, d in TOPICS if s == slug), None)


_LINK = re.compile(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_BREAK = re.compile(r"</?(p|li|ul|ol|h\d)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")


def plain_text(slug: str) -> tuple[str, list[tuple[str, str]]] | None:
    """A topic's body as plain text with its links as "label [url]", plus the (label, url) pairs; for the assistant.

    Renders only the topic's body block, so the text is exactly what the help page shows (D-063 item 2).
    """
    if topic(slug) is None:
        return None
    blocks = get_template(f"support/help/{slug}.html").template.nodelist.get_nodes_by_type(BlockNode)
    body = next(b for b in blocks if b.name == "body")
    markup = body.nodelist.render(Context({"ussd_code": getattr(settings, "USSD_CODE", "")}))
    links = []

    def link(match):
        label = " ".join(html.unescape(_TAG.sub("", match.group(2))).split())
        links.append((label, match.group(1)))
        return f"{label} [{match.group(1)}]"

    text = _BREAK.sub("\n", _LINK.sub(link, markup))
    lines = (" ".join(html.unescape(_TAG.sub("", line)).split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line), links
