# 02 — Product and Users

## Vision
One place where any rental owner in Kenya, from someone with 3 rooms to a management company with 3,000 units, can bill tenants, collect rent, see arrears, and stay compliant, mostly from a phone.

## Who we serve

| Persona | Typical situation | What they need most |
|---------|-------------------|---------------------|
| **Small landlord** (1–20 units) | Notebook/WhatsApp, often lives elsewhere | Who paid, who hasn't, simple reminders |
| **Caretaker** | Collects rent, reports issues, low-end phone | Very simple screens, works on weak data |
| **Property manager / agency** | Manages units for several owners | Per-owner statements, staff roles, commissions |
| **Accountant** | Prepares statements/tax | Clean ledgers, exports, MRI figures |
| **Tenant** | Pays by M-Pesa, wants proof | Receipt, balance, easy payment instructions, maintenance requests |
| **Larger owner / company** | Many buildings | Dashboards, arrears, multi-user permissions |

## Core problems
1. Not knowing who has paid and who owes.
2. Reconciling M-Pesa messages against tenants by hand.
3. Awkward, inconsistent arrears follow-up.
4. No records for disputes, deposits, or tax.
5. Owners who don't live near their property cannot see what is happening.

## MVP scope (must ship)
1. Register/login (phone or email), create an Organization.
2. Add Property → Units (Buildings optional; many rental blocks are just one building).
3. Add Tenants and Leases (rent amount, deposit, due day).
4. Generate monthly rent invoices automatically.
5. Record payments (manual first, then M-Pesa C2B).
6. Tenant balance and arrears list.
7. Receipts (PDF/shareable) and SMS/WhatsApp reminders.
8. Basic dashboard: expected vs collected, vacant units, arrears.
9. Role access: Owner, Manager, Caretaker.

## Explicitly NOT in MVP
AI assistant, analytics beyond one dashboard, utilities metering, accounting module, subscriptions/billing UI, support desk, document vault. They come later (see 04_ROADMAP).

## Kenya-specific features to plan for
- Rent paid by M-Pesa, often to a personal number, Paybill, or Till.
- Deposits (often two months' rent, sometimes plus a water/utility deposit) with refund tracking.
- Water and electricity billed per unit (KPLC prepaid, water bills) — post-MVP.
- Caretaker as an important but low-privilege role.
- Tenants use feature phones (USSD/SMS support is a later opportunity).
- Rent may be due on a fixed day (commonly the 1st–5th).
- Units are often "rooms/bedsitters/1BR" — allow custom unit types.
- Swahili UI option.

## Success measures for the pilot
- 5 pilot landlords onboard in under 15 minutes each.
- Monthly rent cycle run entirely in the app.
- 90%+ of payments reconciled without manual work.
- Pilot users say they'd pay.

## Landlord interview log
_(Fill in during pre-build. Date, landlord type, units, current method, top pain, willingness to pay.)_

| Date | Who | Units | Current method | Top pain | Would pay? |
|------|-----|-------|----------------|----------|------------|
|      |     |       |                |          |            |
