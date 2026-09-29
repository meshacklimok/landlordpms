# 10 — Users, Roles and Permissions

> **Note:** roles are fully editable per organization and gain two more defaults (Leasing/Letting Agent, Maintenance Manager). See [13_ROLES_AND_CAPABILITIES.md](13_ROLES_AND_CAPABILITIES.md). The matrix below describes only the *default templates*. Caretaker cash-payment permission is granted by the Owner/Manager, not fixed.

## Three layers of "who can see what"
1. **Organization** (hard wall): a user only sees data of organizations they belong to. Nothing crosses this line.
2. **Role** (what actions): Owner, Manager, Accountant, Caretaker, Viewer.
3. **Scope** (which properties): a role can apply to all properties in the organization or only to assigned ones.

## Organization types
- **Landlord organization:** one owner with their own properties (most users).
- **Agency / management company:** manages properties on behalf of several property owners. Properties have an optional `client owner`, who gets read-only access to their own properties only.

## Roles

| Role | Who | Scope |
|------|-----|-------|
| **Owner** | Person who created the organization (can be several) | All |
| **Manager** | Property manager, agent, trusted relative | All or assigned properties |
| **Accountant** | Bookkeeper | All (financial view) |
| **Caretaker** | On-site person | Assigned properties only |
| **Viewer** | Co-owner, investor, agency's client owner | Assigned properties, read-only |
| **Tenant** *(Phase 9 portal)* | Renter | Own lease only |
| **Platform staff** | Us (support/admin) | None by default, see below |

## Permission matrix
`✔` allowed, `◐` limited (see note), `✘` no.

| Capability | Owner | Manager | Accountant | Caretaker | Viewer |
|---|:-:|:-:|:-:|:-:|:-:|
| See dashboard totals | ✔ | ✔ | ✔ | ◐ occupancy and arrears count only | ✔ own properties |
| View properties / units | ✔ | ✔ | ✔ | ✔ assigned | ✔ |
| Add/edit properties and units | ✔ | ✔ | ✘ | ✘ | ✘ |
| View tenants (name, phone, unit) | ✔ | ✔ | ✔ | ✔ assigned | ◐ names only |
| View tenant national ID / sensitive docs | ✔ | ✔ | ✘ | ✘ | ✘ |
| Add/edit tenants | ✔ | ✔ | ✘ | ◐ add only, pending approval | ✘ |
| Create/end/renew leases | ✔ | ✔ | ✘ | ✘ | ✘ |
| View invoices and balances | ✔ | ✔ | ✔ | ◐ balance per tenant, assigned | ✔ |
| Create/adjust/void invoices | ✔ | ✔ | ◐ propose, Owner/Manager approves | ✘ | ✘ |
| Record a payment | ✔ | ✔ | ✔ | ◐ cash/manual, flagged for review | ✘ |
| Reverse a payment | ✔ | ◐ up to a set limit | ✘ | ✘ | ✘ |
| Match unmatched M-Pesa payments | ✔ | ✔ | ✔ | ✘ | ✘ |
| Send reminders / messages | ✔ | ✔ | ✘ | ◐ templates only | ✘ |
| Log/see maintenance requests | ✔ | ✔ | ◐ costs only | ✔ | ✘ |
| Record/see expenses | ✔ | ✔ | ✔ | ◐ submit only | ✘ |
| Financial reports / exports | ✔ | ✔ | ✔ | ✘ | ✔ own properties |
| Invite/remove users, change roles | ✔ | ◐ caretakers only | ✘ | ✘ | ✘ |
| M-Pesa / SMS settings | ✔ | ✘ | ✘ | ✘ | ✘ |
| Subscription and billing | ✔ | ✘ | ✘ | ✘ | ✘ |
| View audit log | ✔ | ◐ own actions and team | ✘ | ✘ | ✘ |
| Delete organization / export all data | ✔ | ✘ | ✘ | ✘ | ✘ |

## How many users? Recommended seat limits by plan
Tenants and viewers do not count as seats.

| Plan | Units | Staff seats (Owner + Manager + Accountant + Caretaker) |
|------|-------|-------|
| Free | 5 | 2 (Owner + 1 caretaker) |
| Starter | 25 | 4 |
| Business | 100 | 10 |
| Professional | 300 | 30 |
| Enterprise | custom | unlimited |

Extra seats can be sold as add-ons. These limits are a starting point to test with pilots.

## Rules
1. Every organization must have **at least one Owner**. The last Owner cannot be removed, only replaced (ownership transfer).
2. A user can belong to several organizations, with a different role in each (an accountant serving many landlords). The active organization is switched explicitly.
3. **Scoping:** Manager, Caretaker and Viewer may be limited to chosen properties through a `PropertyAccess` table. Every queryset filters by organization **and** allowed properties.
4. **Sensitive fields** (national ID, documents, deposit details) are hidden from roles without the permission, even when the tenant row is visible.
5. **Financial records are never deleted.** Only voided or reversed, with a reason and the actor recorded in the audit log.
6. **Approvals:** actions marked ◐ create a pending request the Owner or Manager confirms. This protects landlords from caretaker mistakes or fraud.
7. **Caretaker payments** are flagged until confirmed by an Owner or Manager, and appear in a reconciliation queue.
8. Invitations are by phone or email, single-use, and expire. Removing a user ends their sessions immediately.
9. Two-factor authentication is optional for everyone and strongly encouraged for Owners.
10. **Platform staff** (us) cannot see customer data by default. Support access requires the customer to grant time-limited access. Every session is logged and visible to the customer. Superuser accounts are few, MFA-protected, and never shared.
11. Agency clients (Viewers) see only their own properties, and only reports the agency chooses to share.

## Implementation notes
- `Membership(user, organization, role, is_active)` plus `PropertyAccess(membership, property)`.
- Permissions are defined as named capabilities (`payments.record`, `payments.reverse`, `tenants.view_sensitive`, ...) mapped to roles in one place, so the matrix above is a single table in code.
- One reusable view mixin checks: logged in → member of active organization → has capability → object within scope.
- Tests: for each role, a table-driven test asserts allowed and forbidden actions, and cross-organization access returns 404.
