"""The staff menu: a side menu on a laptop, grouped dropdowns in the top bar on a phone or tablet (D-111 update).

Each link shows only to members who can use it; a group with no links is left out. The link for the page being
viewed, and its group, are marked active: the link whose address is the longest start of the current path.
"""

from django.urls import reverse
from django.utils.translation import gettext as _

from .permissions import can


def _first(membership, *options):
    """The first (capability, url name) the member has, for a menu item that opens a different page by role."""
    for capability, name in options:
        if can(membership, capability):
            return name
    return None


def _items(membership, assistant_set_up):
    m = membership
    v = m.organization.vocab
    # (label, url name or None, Bootstrap Icons name for the side menu)
    property_items = [
        (v["properties"], "properties:list" if can(m, "properties.view") else None, "buildings"),
        (v["units"], "properties:units" if can(m, "units.view") else None, "door-closed"),
        (_("Meters"), "meters:list" if can(m, "meters.view") else None, "speedometer2"),
        (_("Maintenance"), "maintenance:list"
         if can(m, "maintenance.view") or can(m, "maintenance.view_assigned") else None, "tools"),
        (_("Documents"), "documents:list" if can(m, "documents.view") else None, "folder2"),
    ]
    people_items = [
        (v["tenants"], "tenants:list" if can(m, "tenants.view") else None, "people"),
        (v["leases"], "leases:list" if can(m, "leases.view") else None, "file-earmark-text"),
        (_("Prospects"), "prospects:list" if can(m, "prospects.view") else None, "person-plus"),
        (_("Conversations"), "conversations:inbox" if can(m, "messages.view") else None, "chat-dots"),
    ]
    money_items = [
        (_("Invoices"), "billing:invoices" if can(m, "invoices.view") else None, "receipt"),
        (_("Payments"), "payments:list" if can(m, "payments.view") else None, "cash-coin"),
        (_("M-Pesa"), _first(m, ("mpesa.match", "mpesa:inbox"), ("mpesa.view_transactions", "mpesa:transactions"),
                             ("mpesa.settings", "mpesa:settings")), "phone"),
        (_("Bank"), _first(m, ("mpesa.match", "banking:inbox"), ("payments.view", "banking:lines")), "bank"),
        (_("Expenses"), "expenses:list" if can(m, "expenses.view") else None, "wallet2"),
        (_("Reports"), _first(m, ("reports.view_basic", "reports:centre"), ("dashboard.view_financial", "reports:dashboard"),
                              ("reports.view_financial", "reports:income")), "bar-chart-line"),
    ]
    assistant = assistant_set_up and m is not None and (m.organization.assistant_enabled
                                                        or can(m, "organization.manage"))
    more_items = [
        (_("Messages"), _first(m, ("messages.view", "notifications:log"),
                               ("templates.manage", "notifications:templates"),
                               ("organization.manage", "notifications:settings")), "chat-left-text"),
        (_("Import"), "imports:list"
         if can(m, "units.manage") or can(m, "tenants.manage") or can(m, "invoices.adjust") else None, "upload"),
        (_("Ask"), "ai_assistant:ask" if assistant else None, "stars"),
        (_("Staff"), "accounts:staff" if can(m, "staff.view") else None, "person-badge"),
        (_("Roles"), "accounts:roles" if can(m, "roles.manage") else None, "shield-lock"),
        (_("Subscription"), "subscriptions:page" if can(m, "subscription.view") else None, "credit-card"),
        (_("Refer a landlord"), "growth:refer" if can(m, "organization.manage") else None, "gift"),
        (_("Settings"), "accounts:organization" if can(m, "organization.manage") else None, "gear"),
    ]
    return [(_("Property"), property_items), (v["tenants"], people_items), (_("Money"), money_items),
            (_("More"), more_items)]


def nav_groups(request, membership, assistant_set_up):
    """[{"label", "active", "items": [{"label", "url", "icon", "active"}]}] for the member, groups with no links left out."""
    if membership is None:
        return []
    groups = []
    for label, items in _items(membership, assistant_set_up):
        links = [{"label": text, "url": reverse(name), "icon": icon, "active": False} for text, name, icon in items if name]
        if links:
            groups.append({"label": label, "items": links, "active": False})
    path = request.path
    matches = [(len(link["url"]), group, link) for group in groups for link in group["items"]
               if path.startswith(link["url"])]
    if matches:
        _length, group, link = max(matches, key=lambda match: match[0])
        group["active"] = link["active"] = True
    return groups
