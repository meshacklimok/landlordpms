"""Capability catalog and default role templates (doc 13).

Capabilities are defined here because the code checks them. ``sync_access_catalog``
copies them into the database; admins bundle them into roles but cannot invent new
behaviour. Default role templates are only *seeded* from here: once a template
exists, the Platform Admin edits it in Django admin and re-syncing never
overwrites those edits.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Cap:
    codename: str
    description: str
    sensitive: bool = False
    # Needs Membership.all_properties (organization-wide power).
    org_wide: bool = False
    # Still allowed when the organization is READ_ONLY (lapsed subscription, doc 14 A12).
    read_only_safe: bool = False

    @property
    def module(self) -> str:
        return self.codename.split(".", 1)[0]


def _view(codename: str, description: str, **kw) -> Cap:
    return Cap(codename, description, read_only_safe=True, **kw)


CAPABILITIES: tuple[Cap, ...] = (
    # organization
    Cap("organization.manage", "Edit organization settings", org_wide=True),
    _view("organization.export_data", "Export all organization data", sensitive=True, org_wide=True),
    # staff and roles
    _view("staff.view", "See staff and their roles", org_wide=True),
    Cap("staff.manage", "Invite staff, assign roles, property access and overrides", org_wide=True),
    Cap("roles.manage", "Create, rename, clone and edit roles", org_wide=True),
    # subscription
    _view("subscription.view", "See the subscription and usage", org_wide=True),
    # Kept usable in read-only mode so a lapsed organization can pay.
    _view("subscription.manage", "Change plan and pay the subscription", org_wide=True),
    # properties
    _view("properties.view", "See properties"),
    Cap("properties.manage", "Add, edit and archive properties, buildings and units"),
    # units and letting
    _view("units.view", "See units"),
    Cap("units.manage", "Add, edit and archive units"),
    Cap("units.set_status", "Set a unit's manual status (reserved, under maintenance, inactive)"),
    _view("units.list_vacant", "See vacancy and availability"),
    # tenants
    _view("tenants.view", "See tenants"),
    Cap("tenants.manage", "Add and edit tenants"),
    _view("tenants.view_sensitive", "See national ID and tenant documents", sensitive=True),
    Cap("tenants.invite_portal", "Invite tenants to the tenant portal"),
    Cap("tenants.issue_letter", "Issue tenancy and payment record letters"),
    # prospects
    _view("prospects.view", "See prospects and viewings"),
    Cap("prospects.manage", "Manage prospects and viewings, convert to tenant"),
    # leases
    _view("leases.view", "See leases"),
    Cap("leases.draft", "Draft leases"),
    Cap("leases.activate", "Activate and renew leases"),
    Cap("leases.terminate", "End and terminate leases"),
    Cap("leases.change_rent", "Record rent changes"),
    # condition reports (D-047)
    _view("inspections.view", "See condition reports, unit item registers and their photos"),
    Cap("inspections.record", "Record move-in and move-out condition reports and edit item registers"),
    # metered water (D-057)
    _view("meters.view", "See water meters, readings and their photos"),
    Cap("meters.record", "Record meter readings"),
    Cap("meters.approve", "Approve or reject meter readings for billing"),
    Cap("meters.manage", "Add and edit meters, their units and rates"),
    # billing
    _view("invoices.view", "See invoices and balances"),
    Cap("invoices.generate", "Generate and issue invoices"),
    Cap("invoices.adjust", "Propose invoice adjustments and credit notes"),
    Cap("invoices.approve_adjustment", "Approve invoice adjustments"),
    Cap("invoices.void", "Void invoices", sensitive=True),
    Cap("charges.manage", "Manage charge types and recurring charges"),
    Cap("deposits.record", "Record deposits received and refunded"),
    Cap("deposits.deduct", "Deduct from a deposit", sensitive=True),
    Cap("arrears.follow_up", "Record calls and promises to pay"),
    # payments
    _view("payments.view", "See payments"),
    Cap("payments.record", "Record payments (cash, bank, cheque)"),
    Cap("payments.confirm", "Confirm payments recorded by others"),
    Cap("payments.reverse", "Reverse payments", sensitive=True),
    Cap("payments.allocate", "Allocate payments to invoices"),
    Cap("receipts.issue", "Issue and resend receipts"),
    # payment accounts
    _view("payment_accounts.view", "See payment accounts", org_wide=True),
    Cap("payment_accounts.manage", "Add and edit payment accounts", sensitive=True, org_wide=True),
    Cap("mpesa.settings", "Edit M-Pesa credentials and settings", sensitive=True, org_wide=True),
    # mpesa
    _view("mpesa.view_transactions", "See M-Pesa transactions"),
    Cap("mpesa.match", "Match payments in the unallocated inbox"),
    # communications
    _view("messages.view", "See the message log"),
    Cap("messages.send", "Send messages to tenants"),
    Cap("messages.send_bulk", "Send bulk announcements"),
    Cap("templates.manage", "Edit message templates"),
    # maintenance
    _view("maintenance.view", "See all maintenance requests"),
    _view("maintenance.view_assigned", "See maintenance requests assigned to me"),
    Cap("maintenance.create", "Create maintenance requests"),
    Cap("maintenance.assign", "Assign maintenance requests"),
    Cap("maintenance.update", "Update maintenance requests"),
    Cap("maintenance.close", "Close maintenance requests"),
    Cap("maintenance.costs", "Record maintenance costs"),
    Cap("contractors.manage", "Manage contractors and suppliers"),
    # expenses
    _view("expenses.view", "See expenses"),
    Cap("expenses.submit", "Submit expenses"),
    Cap("expenses.approve", "Approve expenses"),
    # documents
    _view("documents.view", "See documents"),
    Cap("documents.upload", "Upload documents"),
    Cap("documents.manage", "Edit and archive documents"),
    Cap("documents.share_with_tenant", "Share documents with tenants"),
    # reports and dashboard
    _view("reports.view_basic", "See operational reports"),
    _view("reports.view_financial", "See financial reports"),
    _view("reports.export", "Export reports", sensitive=True),
    _view("dashboard.view_summary", "See the summary dashboard"),
    _view("dashboard.view_financial", "See financial dashboard figures"),
    # audit
    _view("audit.view_own", "See my own activity"),
    _view("audit.view_all", "See the organization's full audit log", org_wide=True),
)

CAPABILITY_MAP: dict[str, Cap] = {c.codename: c for c in CAPABILITIES}
ALL_CODENAMES: frozenset[str] = frozenset(CAPABILITY_MAP)

# The Owner role can never lose these (doc 13 safety rules).
OWNER_CRITICAL: frozenset[str] = frozenset(
    {"roles.manage", "staff.manage", "subscription.manage", "organization.manage"}
)


@dataclass(frozen=True)
class TemplateDef:
    key: str
    name: str
    description: str
    capabilities: frozenset[str]
    is_owner: bool = False


def _caps(*codenames: str) -> frozenset[str]:
    unknown = set(codenames) - ALL_CODENAMES
    if unknown:
        raise ValueError(f"Unknown capabilities in template: {sorted(unknown)}")
    return frozenset(codenames)


_MANAGER = _caps(
    "staff.view",
    "properties.view", "properties.manage",
    "units.view", "units.manage", "units.set_status", "units.list_vacant",
    "tenants.view", "tenants.manage", "tenants.view_sensitive", "tenants.invite_portal", "tenants.issue_letter",
    "prospects.view", "prospects.manage",
    "leases.view", "leases.draft", "leases.activate", "leases.terminate", "leases.change_rent",
    "inspections.view", "inspections.record",
    "meters.view", "meters.record", "meters.approve", "meters.manage",
    "invoices.view", "invoices.generate", "invoices.adjust", "invoices.approve_adjustment",
    "invoices.void", "charges.manage", "deposits.record", "deposits.deduct", "arrears.follow_up",
    "payments.view", "payments.record", "payments.confirm", "payments.allocate", "receipts.issue",
    "payment_accounts.view",
    "mpesa.view_transactions", "mpesa.match",
    "messages.view", "messages.send", "messages.send_bulk", "templates.manage",
    "maintenance.view", "maintenance.view_assigned", "maintenance.create", "maintenance.assign",
    "maintenance.update", "maintenance.close", "maintenance.costs", "contractors.manage",
    "expenses.view", "expenses.submit",
    "documents.view", "documents.upload", "documents.manage", "documents.share_with_tenant",
    "reports.view_basic", "reports.view_financial", "reports.export",
    "dashboard.view_summary", "dashboard.view_financial",
    "audit.view_own",
)

# `●` cells from the doc 13 matrix. `◐` cells are left out: they are toggles an
# Owner can switch on per role or per person.
ROLE_TEMPLATES: tuple[TemplateDef, ...] = (
    TemplateDef("owner", "Owner", "Full control of the organization.", ALL_CODENAMES, is_owner=True),
    TemplateDef("manager", "Manager", "Runs day-to-day operations.", _MANAGER),
    TemplateDef(
        "accountant",
        "Accountant",
        "Invoices, payments, reconciliation and financial reports.",
        _caps(
            "properties.view", "units.view", "units.list_vacant", "tenants.view", "leases.view",
            "meters.view", "meters.approve",
            "invoices.view", "deposits.record", "arrears.follow_up",
            "payments.view", "payments.record", "payments.allocate", "receipts.issue",
            "payment_accounts.view", "mpesa.view_transactions", "mpesa.match",
            "expenses.view", "expenses.submit", "expenses.approve",
            "reports.view_basic", "reports.view_financial", "reports.export",
            "dashboard.view_summary", "dashboard.view_financial", "audit.view_own",
        ),
    ),
    TemplateDef(
        "caretaker",
        "Caretaker",
        "On-site: sees assigned properties, tenants and reports repairs.",
        _caps(
            "properties.view", "units.view", "units.list_vacant", "tenants.view",
            "inspections.view", "inspections.record", "meters.view", "meters.record",
            "maintenance.view", "maintenance.create", "dashboard.view_summary", "audit.view_own",
        ),
    ),
    TemplateDef(
        "leasing_agent",
        "Leasing / Letting Agent",
        "Fills vacancies: prospects, tenants and lease drafts.",
        _caps(
            "properties.view", "units.view", "units.list_vacant",
            "tenants.view", "tenants.manage", "prospects.view", "prospects.manage",
            "leases.view", "leases.draft", "inspections.view", "inspections.record",
            "documents.view", "documents.upload",
            "dashboard.view_summary", "audit.view_own",
        ),
    ),
    TemplateDef(
        "maintenance_manager",
        "Maintenance Manager",
        "Runs repairs: creates and assigns jobs, contractors and costs.",
        _caps(
            "properties.view", "units.view", "units.list_vacant", "inspections.view",
            "maintenance.view", "maintenance.view_assigned", "maintenance.create", "maintenance.assign",
            "maintenance.update", "maintenance.close", "maintenance.costs", "contractors.manage",
            "expenses.view", "expenses.submit", "dashboard.view_summary", "audit.view_own",
        ),
    ),
    TemplateDef(
        "maintenance_staff",
        "Maintenance Staff",
        "Sees and updates only the jobs assigned to them.",
        _caps(
            "units.view", "units.list_vacant", "maintenance.view_assigned", "maintenance.create",
            "maintenance.update", "dashboard.view_summary", "audit.view_own",
        ),
    ),
    TemplateDef(
        "viewer",
        "Viewer",
        "Read-only access to properties, balances and reports.",
        _caps(
            "properties.view", "units.view", "units.list_vacant", "leases.view", "inspections.view",
            "meters.view", "invoices.view", "payments.view",
            "reports.view_basic", "reports.view_financial", "reports.export",
            "dashboard.view_summary", "dashboard.view_financial", "audit.view_own",
        ),
    ),
)
