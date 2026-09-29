# 13 — Roles and Capabilities (fully editable)

Supersedes the fixed role table in doc 10 wherever they differ. Doc 10's matrix now describes only the **default templates**.

## Why fully editable
Every organization runs differently:
- A large agency has Owner, Manager, Accountant, Leasing Agent, Maintenance Manager and several caretakers.
- A small landlord has just an Owner and one caretaker, or Owner + Manager + Caretaker.
- One caretaker only reads balances; another collects cash and reports repairs.

So no role has fixed powers. Roles are data, and each organization edits its own.

## The model

```
Capability        (codename, module, description)            defined in code, synced to DB
RoleTemplate      (name, description, capabilities)          platform defaults, edited by Platform Admin
Role              (organization, name, based_on_template?, is_owner_role, is_active)
RoleCapability    (role, capability)
Membership        (user, organization, role, all_properties, is_active)
MembershipCapability (membership, capability, granted: bool) per-person override
PropertyAccess    (membership, property)
```

### How it works
1. **Templates** are the starting kits: Owner, Manager, Accountant, Caretaker, Leasing Agent, Maintenance Manager, Maintenance Staff, Viewer. The Platform Admin can edit them in Django admin; changes affect only *new* organizations.
2. **When an organization is created, templates are copied into that organization as its own roles.** After that, the organization edits its copies freely and never affects other organizations.
3. Organizations can **rename, add, clone and remove** roles (e.g. "Estate Supervisor", "Rent Collector").
4. **Per-person overrides:** an Owner or Manager can grant or withhold one capability for one person without creating a new role (e.g. let *this* caretaker record cash payments).
5. **Effective capabilities** = role capabilities + granted overrides − denied overrides, limited to the person's property scope.
6. A user has **one role per organization** (plus overrides). Simple to reason about and audit. Different organizations can give the same user different roles.

### Safety rules (so flexibility doesn't become a hole)
- **No privilege escalation:** you can only grant capabilities you hold yourself. A Manager cannot give a caretaker `subscription.manage` if the Manager doesn't have it.
- **Who may edit roles and grants:** holders of `roles.manage` (Owner by default). Holders of `staff.manage` can invite people and assign existing roles/overrides within their own limits.
- **Owner role is protected:** it always keeps the critical capabilities (`roles.manage`, `staff.manage`, `subscription.manage`, `organization.manage`). Every organization needs at least one active Owner.
- You cannot demote or remove yourself if you are the last Owner.
- Deleting a role requires reassigning its members first. Roles used in history are deactivated, not deleted.
- **Every change to roles, grants and memberships is audited** (who, what, old → new).
- Capability changes take effect on the next request (no stale sessions).
- Sensitive capabilities are flagged (`sensitive=True`): tenant ID documents, M-Pesa settings, exports, payment reversal. The UI warns when granting them.

### Approval by permission, not by role
Caretaker cash payment case, solved with two capabilities:
- `payments.record` lets you enter a payment.
- `payments.confirm` lets you approve it.
A payment recorded by someone **without** `payments.confirm` enters `PENDING_REVIEW` until an Owner/Manager confirms. Give a trusted caretaker both and it is confirmed immediately. Same pattern for other maker/checker pairs (`invoices.adjust` / `invoices.approve_adjustment`, `expenses.submit` / `expenses.approve`).

This answers decision 1: **the caretaker can record payments only if the Owner or Manager grants `payments.record`** (role-wide or for one person). Default caretaker template: not granted.

## Capability catalog (initial)
Names are `module.action`. Views check these, never role names.

| Module | Capabilities |
|---|---|
| organization | `organization.manage`, `organization.export_data` |
| staff & roles | `staff.view`, `staff.manage`, `roles.manage` |
| subscription | `subscription.view`, `subscription.manage` |
| properties | `properties.view`, `properties.manage` (add/edit/archive property, buildings, units) |
| units & letting | `units.view`, `units.manage`, `units.set_status`, `units.list_vacant` (see vacancy/availability) |
| tenants | `tenants.view`, `tenants.manage`, `tenants.view_sensitive`, `tenants.invite_portal` |
| prospects | `prospects.view`, `prospects.manage` (leads/viewings, convert to tenant) |
| leases | `leases.view`, `leases.draft`, `leases.activate`, `leases.terminate`, `leases.change_rent` |
| billing | `invoices.view`, `invoices.generate`, `invoices.adjust`, `invoices.approve_adjustment`, `invoices.void`, `charges.manage` |
| payments | `payments.view`, `payments.record`, `payments.confirm`, `payments.reverse`, `payments.allocate`, `receipts.issue` |
| payment accounts | `payment_accounts.view`, `payment_accounts.manage`, `mpesa.settings` |
| mpesa | `mpesa.view_transactions`, `mpesa.match` (unallocated inbox) |
| communications | `messages.view`, `messages.send`, `messages.send_bulk`, `templates.manage` |
| maintenance | `maintenance.view`, `maintenance.view_assigned`, `maintenance.create`, `maintenance.assign`, `maintenance.update`, `maintenance.close`, `maintenance.costs`, `contractors.manage` |
| expenses | `expenses.view`, `expenses.submit`, `expenses.approve` |
| documents | `documents.view`, `documents.upload`, `documents.manage`, `documents.share_with_tenant` |
| reports | `reports.view_basic`, `reports.view_financial`, `reports.export` |
| dashboard | `dashboard.view_summary`, `dashboard.view_financial` |
| audit | `audit.view_own`, `audit.view_all` |

Maintenance and expenses capabilities exist from the start, so roles can be designed now, even though those modules ship later. Unused capabilities do nothing.

## Default role templates (starting points)
`●` granted · `○` not granted · `◐` optional toggle offered in the UI when assigning.

| Capability group | Owner | Manager | Accountant | Caretaker | Leasing / Letting Agent | Maintenance Manager | Maintenance Staff | Viewer |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| organization / roles / subscription | ● | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| staff.manage | ● | ◐ | ○ | ○ | ○ | ○ | ○ | ○ |
| properties.view | ● | ● | ● | ● (assigned) | ● | ● | ◐ assigned | ● |
| properties.manage | ● | ● | ○ | ○ | ○ | ○ | ○ | ○ |
| units.view / list_vacant | ● | ● | ● | ● | ● | ● | ● assigned | ● |
| units.manage / set_status | ● | ● | ○ | ◐ | ◐ | ◐ maintenance status | ○ | ○ |
| tenants.view | ● | ● | ● | ● | ● | ◐ names, phones | ◐ | ◐ |
| tenants.manage | ● | ● | ○ | ◐ | ● | ○ | ○ | ○ |
| tenants.view_sensitive | ● | ● | ○ | ○ | ◐ | ○ | ○ | ○ |
| prospects.* | ● | ● | ○ | ○ | ● | ○ | ○ | ○ |
| leases.draft | ● | ● | ○ | ○ | ● | ○ | ○ | ○ |
| leases.activate / terminate / change_rent | ● | ● | ○ | ○ | ◐ | ○ | ○ | ○ |
| invoices.view | ● | ● | ● | ◐ balances | ◐ | ○ | ○ | ● |
| invoices.generate / adjust / void | ● | ● | ◐ propose | ○ | ○ | ○ | ○ | ○ |
| payments.view | ● | ● | ● | ◐ | ○ | ○ | ○ | ● |
| payments.record | ● | ● | ● | ◐ **owner/manager decides** | ○ | ○ | ○ | ○ |
| payments.confirm / reverse | ● | ● / ◐ limit | ◐ | ○ | ○ | ○ | ○ | ○ |
| mpesa.match | ● | ● | ● | ○ | ○ | ○ | ○ | ○ |
| mpesa.settings / payment_accounts.manage | ● | ◐ | ○ | ○ | ○ | ○ | ○ | ○ |
| messages.send | ● | ● | ○ | ◐ templates | ◐ | ◐ maintenance | ○ | ○ |
| maintenance.view / create | ● | ● | ○ | ● | ○ | ● | ● assigned | ○ |
| maintenance.assign / close / costs / contractors | ● | ● | ◐ costs | ○ | ○ | ● | ○ | ○ |
| expenses.submit | ● | ● | ● | ◐ | ○ | ● | ○ | ○ |
| expenses.approve | ● | ◐ | ● | ○ | ○ | ○ | ○ | ○ |
| documents.view / upload | ● | ● | ◐ | ◐ | ● (lease docs) | ◐ | ○ | ○ |
| reports.view_financial / export | ● | ● | ● | ○ | ○ | ○ | ○ | ● |
| dashboard.view_financial | ● | ● | ● | ○ | ○ | ○ | ○ | ● |
| audit.view_all | ● | ◐ | ○ | ○ | ○ | ○ | ○ | ○ |

### New roles explained
- **Leasing / Letting Agent:** fills vacancies. Sees vacant units and prospects, registers prospective tenants, drafts leases, and may be allowed to activate them. Does not see collection reports or M-Pesa.
- **Maintenance Manager:** runs all repairs across the organization or assigned properties: creates and assigns requests, manages contractors, records repair costs and closes jobs. Cannot see rent or payments unless granted.
- **Maintenance Staff:** sees and updates only jobs assigned to them (formerly "Maintenance/David").

## Small organization examples
| Organization | Setup |
|---|---|
| Solo landlord + caretaker | Owner (all). Caretaker role with `properties.view`, `tenants.view`, `invoices.view` balances, `maintenance.create`; Owner ticks `payments.record` if wanted. |
| Owner + Manager + Caretaker | Manager holds day-to-day capabilities; Caretaker as above. No Accountant role needed. |
| Agency | Owner, Managers per portfolio (scoped by `PropertyAccess`), Accountant, Leasing Agents, Maintenance Manager, many Caretakers. |
| Caretaker doing extra | Same role as another caretaker + a per-person override or two. No new role needed. |

## Scope (which properties)
`Membership.all_properties = true` or explicit `PropertyAccess` rows. A capability only applies inside the person's scope. Organization-wide capabilities (staff, roles, subscription, payment accounts) require `all_properties`.

## Effective permission check (one function)
```
can(membership, capability, property=None):
    active membership in the organization AND organization active
    capability in (role capabilities + granted overrides − denied overrides)
    if property given: membership.all_properties or property in PropertyAccess
```
Every view, service, API and AI tool calls this same function.

## Testing requirements
- Table-driven test per default template.
- Escalation test: a Manager cannot grant what they lack.
- Last-Owner protection test.
- Override test: caretaker without `payments.record` is blocked; with a grant, payment goes to `PENDING_REVIEW` unless they also hold `payments.confirm`.
- Cross-organization: editing Organization A's role never changes Organization B's.
- Scope test: a scoped Manager cannot see unassigned properties.
