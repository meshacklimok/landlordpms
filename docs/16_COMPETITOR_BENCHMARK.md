# 16 — Competitor Benchmark and Lessons

Research date: 2026-09-26, from public web pages only (vendor sites, vendor blogs, tax-advisory articles). **Most sources are vendor marketing**; treat claims, prices and statistics as indicative, not verified. Nothing here is from using the products. Items marked **[VERIFY]** need confirmation from Safaricom, KRA, or a hands-on trial.

## 1. Market map

### Kenya (direct competitors)
| Product | Indicative price (KSh/month) | Positioning | Notable |
|---|---|---|---|
| Pangoni | Free (up to 3 units) to about 6,000; lifetime licence listed | All-in-one, marketplace | Daraja M-Pesa, invoices and receipts, tenant portal, role-based access, API, eTIMS content |
| Nyumba Zetu | about 2,000 to 6,000 | Mobile-first landlords, agents, estates | General ledger, tenant and owner portals, eTIMS integration claimed, onboarding under 20 minutes claimed |
| Bomahut | about 3,000 to 12,000 | Multi-property portfolios | Reports by property/unit, SMS, dated interface (per comparison) |
| EazzyRent | about 2,500 to 8,000 | Budget small landlords | Basic; no tenant portal (per comparison) |
| PMS.co.ke | about 4,000 to 15,000 | Management companies | Management fee calculation, white-label reports, complex leases |
| Blocks | about 5,000 to 20,000 | Estates/body corporates | Service charge, vendor payments, AGM statements |
| Shiftenant | about 3,000 to 10,000 | Tenant screening | Payment history, risk data |
| HomeManager, RentalDesk, EZEN, buniva | listed from about 7,500 | Various | Not reviewed in depth |

### Rest of Africa
ZubaRent (multi-currency, multi-country), Property360 and Porchplus (Nigeria; Porchplus does reminders and OTP over WhatsApp), Hutstack, OurProperty NG, HouseRent Africa (screening, legal docs, marketplace), Safoa (WhatsApp AI assistant, mobile money, rent roll).

### Global leaders
Buildium, AppFolio, Propertyware, RealPage, Yardi, TenantCloud, Rentec Direct, Hemlane. None has M-Pesa or KRA support (Buildium is listed without either), so they are references for depth, not competitors.

## 2. Feature benchmark (what the leaders standardise)
Accounting and general ledger, owner portal and owner statements, tenant portal (pay, request maintenance, statements, documents), maintenance work orders with vendors, financial reports, tenant screening, e-signature leases, listings/marketplace, mobile apps for managers, owners and residents, open API, AI assistance (Buildium and AppFolio lead here).

## 3. How we compare

| Area | Market norm | Our current design | Verdict |
|---|---|---|---|
| M-Pesa collection and auto-matching | Universal | Designed in depth (doc 11 §12–15, Phase 6) | On par; execution matters |
| Editable roles, caretaker cash control, maker/checker | Role-based access, thin | Capability roles, review queue, tenant SMS on cash (doc 13, 14 B4) | **Differentiator** |
| Deposit sub-ledger and clearance statement | Rarely detailed | A1 accepted | **Differentiator** |
| Three separated money flows and append-only ledger | Not visible publicly | Doc 11 §22 | Strong basis for trust |
| eTIMS / KRA | Claimed by leaders | Design-in only (D-034, D-037) | **Gap, see §4** |
| Tenant portal | Most mid-tier tools | Phase 7 | Late; consider earlier |
| Owner portal and statements (agency) | PMS.co.ke, Nyumba Zetu | Design-in only | Gap for agencies, acceptable for v1 |
| Marketplace / listings | Pangoni | Not planned | Skip in v1 (different business) |
| Tenant screening | Shiftenant, HouseRent | Deferred (B9) | Skip v1 |
| WhatsApp channel | Porchplus, Safoa | Adapter designed, Phase 9 | Consider earlier |
| Pricing | Tiered by property/unit count, free small tier, annual discount | Doc 05 | Add a free tier for tiny landlords |
| Water/metered utilities | Not clearly offered by Kenyan tools | Phase 9 | **Opportunity**, move earlier |
| Analytics | Basic to moderate | Doc 15 | Opportunity |

## 4. Findings that change or sharpen our design

1. **eTIMS/eRITS is more central than we assumed.** Sources say landlords in the MRI band (7.5% of gross residential rent on KSh 288,000 to 15,000,000 a year) must register on eTIMS and issue eTIMS-compliant receipts, and KRA is moving residential landlords to a dedicated eRITS system in 2026 **[VERIFY all figures and dates with KRA or an accountant]**. Compliance is a purchase driver in vendor marketing. **Recommendation:** keep D-034 (fields now), but raise the priority: a Phase 0 task to confirm requirements with an accountant, an `EtimsAdapter` interface in the architecture, and a decision on whether eTIMS submission lands before public launch (see D-039).
2. **The C2B callback phone number may be hashed.** A source notes some C2B configurations return a SHA-256 hash of the phone, not the real number **[VERIFY with Safaricom docs and a sandbox test]**. Design change: `MpesaTransaction` stores `msisdn_raw` and `msisdn_hash`; matching by phone must work with hashes (hash known tenant/payer numbers the same way), and we cannot rely on showing real numbers.
3. **Blank or wrong account references are common.** One source estimates about 15% of tenants leave the reference blank [unverified]. The matching order (reference, then payer phone, then amount) stays, but the unallocated inbox is a **first-class daily screen**, not an edge case, and an SMS "we received your payment but could not match it, reply with your unit number" is worth building.
4. **Validation URL is optional and must be requested from Safaricom**, so we cannot count on rejecting bad references at payment time **[VERIFY]**. Reconciliation after the fact is the safe assumption.
5. **Both Paybill and Till must be supported** by the market; our Paybill preference (D-014) stays, but Till users still need a manual or statement-import path.
6. **Landlord time savings are the core pitch** (a claim of 4 to 6 hours a month on 30 units [unverified]). Onboarding speed is the competitor claim to beat: aim for first invoice within 20 minutes; measure it (doc 14 C11).
7. **Pricing by property/unit tiers, free tiny tier, annual discount** is the market norm. Avoid hidden per-transaction fees; global leaders are criticised for them.
8. **Common complaints in this software category:** slow support, non-intuitive UI, onboarding complexity, fees passed to tenants, slow payouts. Our counters: WhatsApp/phone support in pilots, guided setup and CSV import, no tenant fees, direct-to-landlord payments (D-038).
9. **Notable competitor features we lack:** service-charge billing for estates (Blocks), management-fee calculation (PMS.co.ke), tenant payment-history sharing (Shiftenant). Keep as later modules; service charge is already covered by `LeaseCharge`.

## 5. Where we can win
- Trust: separated flows, append-only ledger, deposit clearance, fraud controls for caretakers.
- Fit for how Kenya really operates: caretakers, shared payers, mixed phones, cash, small buildings.
- Compliance readiness that is honest and staged.
- Simplicity on low-end phones, Swahili, WhatsApp.
- Analytics landlords can act on (doc 15).

## 6. Recommended next research (before launch)
1. Hands-on trials: sign up for Pangoni, Nyumba Zetu, one of Bomahut/EazzyRent; record onboarding time, screens, and pain points.
2. Interview 5 to 10 landlords and caretakers about what they use today (already in TODO Phase 0).
3. Read the current Daraja C2B documentation and test the sandbox for the phone-hash behaviour.
4. Ask an accountant for the exact eTIMS/eRITS/MRI obligations for small landlords and what an app may automate.
5. Screenshot library of the ten key screens from competitors to inform our sketches (doc 14 C2).

## Sources
- [Pangoni: best property management software in Kenya](https://pangoni.io/blog/comparisons/best-property-management-software-kenya)
- [Pangoni: Kenya buyer's guide](https://pangoni.io/blog/guides/property-management-software-kenya)
- [Nyumba Zetu](https://www.nyumbazetu.com/property-management-software-kenya)
- [Buildium: best property management software](https://www.buildium.com/blog/best-property-management-software-2027/)
- [TenantCloud: top software compared](https://www.tenantcloud.com/property-management/top-property-management-software)
- [Global Law Experts: residential rental income rules Kenya](https://globallawexperts.com/kenya-residential-rental-income-rules-2026/)
- [EY: KRA launches eRITS](https://www.ey.com/en_gl/technical/tax-alerts/kenya-revenue-authority-kra-launches-erits-to-enhance-rental-income-tax-compliance)
- [Pangoni: eTIMS for landlords](https://pangoni.io/blog/compliance/kra-etims-landlords-kenya)
- [DEV: M-Pesa auto-reconciliation in Django](https://dev.to/brian_ambeyi_533afa8e6873/how-i-built-m-pesa-payment-auto-reconciliation-in-django-daraja-api-4gkc)
- [Africa platforms: ZubaRent](https://www.zubarent.com/), [Porchplus](https://www.porchplus.com/), [Safoa](https://www.mysafoa.com/), [Property360](https://www.property360.africa/)

---

# Part 2 — Deep dive: what we forgot and what to advance

Second pass over the feature pages of Pangoni, Nyumba Zetu, PMS.co.ke, RentalDesk, EZEN, Safoa and ZubaRent (fetched 2026-09-26). Same caveat: vendor claims, not tested. Their own statistics (for example Nyumba Zetu's 95.3% average collection rate and 86% referral acquisition) are marketing and unverified.

## 7. What we forgot (gaps in docs 10–15)

| # | Gap | Seen at | Why it matters | Proposed handling |
|---|---|---|---|---|
| 1 | **Vacancy-to-lease pipeline**: listings, enquiries, viewings, applications, reservations | Pangoni (prospects, viewings, offers), RentalDesk | We model a vacant unit but nothing that fills it. The Leasing Agent role has no workflow | Add `Prospect` and `Viewing` (light CRM) in Tier 2; shareable public vacancy link and WhatsApp share in the MVP |
| 2 | **Bed-space and hostel units**: room/bed assignment, semester billing | Pangoni | Student hostels are a big Kenyan segment; our Unit assumes one lease per unit | Unit type `BED_SPACE` (parent room) and billing period `SEMESTER`; design-in now, build later |
| 3 | **Estates, HOAs and service charge with a committee** | Nyumba Zetu, Blocks, PMS.co.ke, Pangoni | Gated communities and body corporates are separate customers with committee roles, approvals and common-area budgets | Design-in `Property.category = ESTATE`, a `COMMITTEE` role template, shared-cost split (B3). Not in MVP |
| 4 | **Real accounting outputs**: P&L, cash flow, aged receivables, trial balance, balance sheet | Nyumba Zetu (double-entry GL), PMS.co.ke | Owners, accountants and banks ask for P&L and cash flow, not only rent reports | MVP: P&L and cash flow by property from flows B and C, plus aged receivables. Full double-entry later (D1 leaves room) |
| 5 | **Bank side**: statement import, deposit tracking, multi-bank collection | Nyumba Zetu (12 banks), PMS.co.ke, EZEN | Many landlords collect by bank transfer or cheque, not only M-Pesa | `Payment.method = BANK`, CSV statement import through the same matching engine and unallocated inbox. Bank APIs later |
| 6 | **Accounting export**: QuickBooks and similar | Nyumba Zetu | Accountants and management companies need handover | Standard CSV export now, QuickBooks/Zoho adapter later |
| 7 | **Collectability score** (A to E by likelihood to pay) and prioritised arrears | Nyumba Zetu | Tells a landlord who to call first | Compute from days-late history (doc 15); rule-based first, no ML |
| 8 | **Owner portal and owner statements** | Nyumba Zetu, RentalDesk, Safoa, PMS.co.ke | Diaspora and multi-owner clients are a named segment. Our agency mode is design-in only | Read-only `Owner` viewer membership plus a monthly owner statement PDF in Tier 2, ahead of full agency mode |
| 9 | **Lease documents**: templates, e-signature, demand letters, references, move-out statements | PMS.co.ke, Safoa | Landlords lose time on paperwork; our B9/B10 are deferred | Move lease PDF generation and the move-out statement into Tier 2. E-signature later (legal validity **[VERIFY]**) |
| 10 | **Vendor management**: assign vendor, SLA timers, vendor payments | PMS.co.ke | We have Supplier and Expense but no SLA or vendor workflow | Add `assigned_supplier`, `due_by` and status timestamps to MaintenanceRequest; photo-first reporting and urgency triage (Safoa) |
| 11 | **WhatsApp as a channel and a tenant assistant** | Nyumba Zetu, Safoa, Porchplus | Tenants read WhatsApp, not email; a bot answering "when is rent due, what is my balance?" cuts support load | Move the WhatsApp adapter into Phase 5 as an option; FAQ/AI bot later through the permission-aware layer |
| 12 | **Assisted onboarding and free data migration** | Pangoni (free import up to 200 tenants in one business day), Nyumba Zetu (20-minute self-service) | Setup effort is the main adoption barrier | Internal "import concierge" for pilots; CSV template with validation preview (B8); measure time to first invoice |
| 13 | **Segmented bulk SMS** (by arrears, lease expiry, building) | Safoa, Pangoni | Announcements work better targeted | Extend Announcement (B6) with saved segments |
| 14 | **MFA for sensitive roles** | PMS.co.ke | Financial data and payment-account settings deserve stronger login | Bring optional TOTP/SMS second factor forward for Owner and Accountant; enforce for Platform Admin |
| 15 | **Status page, uptime and trust pages** | RentalDesk (system status), Nyumba Zetu (ODPC, audit logs shown) | Trust with money handling is the sale | Public status page, security and data-protection page, ODPC registration number on the site |
| 16 | **Referral and growth loop** | Nyumba Zetu (86% referral, claimed) | Word of mouth is the main channel in this market | Referral credit (free month) per successful referral |
| 17 | **VAT on our own subscription** | Pangoni prices are "exclude 16% VAT" | Flow A invoices may carry VAT; price display and PlatformReceipt must handle it | Add `vat_amount` to SubscriptionInvoice; confirm registration and eTIMS for our company **[VERIFY]** |
| 18 | **Per-unit price ladders** | Nyumba Zetu (KES 180 down to 60 per unit, 20% annual discount) | Per-unit pricing with volume tiers is used at scale; portfolio tiers for small landlords | Doc 05 to compare portfolio tiers vs per-unit ladder, free tier up to 3 units |
| 19 | **Multi-branch and white-label for agencies** | PMS.co.ke, Pangoni Enterprise | Agencies want their own branding and branches | Design-in `Organization.branch` concept and branding fields; build later |
| 20 | **Short-stay/Airbnb and prospect deals** | Pangoni | Different business (nightly rates, calendars) | **Skip** |

## 8. What to advance (be better than them, not just equal)

1. **Trust as the product.** No competitor page mentions an append-only ledger, reversal-only corrections, deposit clearance, or caretaker maker/checker. Lead with these and show the audit trail to the user.
2. **A collection engine, not just a record.** Combine a collectability score, an arrears priority list, timed reminder sequences, the unmatched-payment SMS, and a daily "who to call today" screen.
3. **Onboarding as a feature.** Import concierge, a 20-minute target, opening balances (A2) and a printable "how to pay" card per building (C12). Time to first invoice is our headline metric.
4. **Honest compliance.** eTIMS, MRI estimates and owner statements behind adapters, with clear labels where professional advice is needed.
5. **Water and utilities done properly.** Meter readings by the caretaker on a phone with photo proof, and split of shared bills; competitors barely mention it.
6. **Caretaker-first mobile UX.** Offline queue, small screens, Swahili, photo capture. Competitors market to landlords and managers; caretakers are the daily users.
7. **Analytics that answer questions**, and an AI assistant restricted to permissioned metrics (doc 15), not a generic chatbot.
8. **Owner transparency.** A read-only owner view and monthly statement so absent and diaspora owners trust their manager. This also makes us sticky to agencies.

## 9. Suggested placement (decision D-040)

| Tier | Additions |
|---|---|
| **MVP (Tier 1)** | Shareable vacancy link, P&L and cash flow by property, bank as a payment method with CSV statement import, import concierge, optional MFA for Owner/Accountant, status and trust pages, VAT field on platform invoices |
| **Tier 2** | Owner viewer and monthly owner statement PDF, lease PDF and move-out statement, Prospect/Viewing, vendor SLA on maintenance, segmented announcements, WhatsApp adapter, collectability score, QuickBooks-friendly export |
| **Design-in only** | BED_SPACE units and semester billing, ESTATE category and committee role, branch/white-label fields, double-entry accounting |
| **Later** | E-signature, WhatsApp AI assistant, bank APIs, Zoho/QuickBooks integrations, opt-in tenant payment reference |
| **Skip** | Short-stay/Airbnb, US-style tenant credit screening, public marketplace |

## Additional sources
[Pangoni](https://pangoni.io/), [Nyumba Zetu](https://www.nyumbazetu.com/), [PMS.co.ke](https://pms.co.ke/), [RentalDesk](https://rentaldesk.co.ke/property-management-software-kenya), [EZEN guide](https://www.ezenfinancials.com/property/blog-property-management-software-kenya), [Safoa](https://www.mysafoa.com/), [ZubaRent](https://www.zubarent.com/)

---

# Part 3 — Third pass: 21 websites and apps (2026-09-29)

Scope: 21 products crawled on 2026-09-29, including mobile apps. Pages were read through a fetcher, so the content is what each vendor publishes. As in Parts 1 and 2, these are vendor claims: nothing was tested hands-on. Play Store pages could not be read by the fetcher, so apps are described from their vendors' pages and from review round-ups. Venco's page gave no feature list and is left out.

## 10. Who was crawled

| # | Product | Where | Type | What is new compared with Parts 1–2 |
|---|---|---|---|---|
| 1 | Rent Manager Kenya | KE | Web | Tenant **payment link** plus STK push; **commission per transaction, no monthly fee**; instant M-Pesa payout to the landlord |
| 2 | Silqu | KE | Web + client and agent apps | **Checks M-Pesa messages and bank statements against records to catch edited receipts**; caretaker portal, security portal, visitor management; water billing; penalty management; diaspora programme |
| 3 | RentLynk | KE | Web + iOS/Android | **"Lipa Mdogo" rent in instalments** into a tenant wallet; offline tenant records in the app; cards and Airtel Money; every tier has every feature, priced only by property count (KSh 2,499 / 4,499 / 6,999); 30% affiliate commission |
| 4 | EzRent | KE | Web | Nearly our own design: signed Daraja callbacks, idempotent processing, double-entry ledger, never holds rent, English and Swahili tenant portal with 2FA, before/on/after due-date nudges; **founder-led onboarding with pilot pricing locked for 12 months**; KSh 2,500 / 7,500 / 15,000 |
| 5 | Bomahut | KE | Web | Named bank integrations (Equity, KCB, Co-operative); also sells to **service-charge managers, garbage collectors and water-billing companies** |
| 6 | Pangoni | KE | Web | Now prices by customer type: landlords KSh 1,500–3,500 (or 45,000 lifetime, free up to 3 units), solo agents, agencies (3–10 seats, 1,000 units), enterprise from KSh 30,000; hostels, estates, diaspora and brokerage pipelines |
| 7 | Nyumba Zetu | KE | Web + mobile web | Collectability bands A–E, trial balance and balance sheet, QuickBooks, an **AI chatbot on WhatsApp**, committee approvals for estates |
| 8 | HomeManager (Buniva) | KE | Web + app | **Caretaker app**: meter readings with timestamp, automatic consumption and **flags for negative or extreme readings**; repairs with photos and priority; vacancy and reservation check before a viewing; caretakers see no money. KSh 7,500 / 18,000 / 45,000, caretakers free |
| 9 | ShifTenant | KE | Web + app | **Smart (IoT) water and power meters**: live readings, remote on/off, leak alerts, tenants buy tokens in the app |
| 10 | Renters Hub | KE | Web app (TWA) | National listings marketplace with verified posters; "Rental Books" for landlords; **rental consultants earn KSh 500 per listing** they bring |
| 11 | Boyot | KE (from the Middle East) | Web + app | Payment link on every invoice, offline and online collection, landlord–tenant messaging |
| 12 | PayProp | ZA/UK/US | Web + app | Match, reconcile and **pay owners out the same day**; **interest charged on arrears automatically**; audit log; used by 2,500+ agencies |
| 13 | Gate Africa | NG | Web + resident app | Estates: **one-time and event guest codes**, dues, prepaid energy top-up, notice board, forum, emergency contacts, domestic-staff register, estate ID cards, alarms |
| 14 | OurProperty NG | NG | Web + client, admin and desktop apps | White-label, multi-branch, staff activity monitoring; developer (off-plan sales, payment plans) and short-stay modules |
| 15 | Porchplus | NG/GH | Web + app | **Deposit escrow**, **move-in inspections with checklist and photos**, **asset register per unit** (furniture, appliances, condition), e-signed leases, rent paid a year in advance with a schedule, rent discounting, interest-earning wallet; free up to 5 units |
| 16 | RentRedi | US | Mobile-first | Autopay, cash at retail shops, **rent reported to credit bureaus**, unlimited teammates, one price for everything |
| 17 | Baselane | US | Web + app | **Bank account per property**, automatic categories for bookkeeping, **tax pack per property** |
| 18 | Landlord Studio | US/UK | Mobile-first | **Receipt photo capture** for expenses, bank feeds, 15+ reports, listing syndication, free tier |
| 19 | DoorLoop | US | Web + app | AI assistant for tenants, **AI inspection reports**, workflow templates, free data migration, open API and Zapier |
| 20 | TurboTenant | US | Web + app | **Condition reports at move-in and move-out**, AI listing text, AI lease check, free for landlords (tenants pay fees) |
| 21 | Buildium | US | Web + resident and owner apps | Resident centre, owner portal, HOA violations, inspections in the field, free property-manager websites |

Supporting (not products): the Buniva water-billing guide, Pangoni's eRITS guide, KRA's rental-income page and a law firm's note on the Finance Act 2026 (see Sources).

## 11. Patterns across all 21

1. **Every Kenyan product is web first; apps are thin.** Only Silqu, RentLynk and HomeManager publish real Kenyan apps, and tenants are never asked to install one (payment links, SMS, web portal). This confirms our choice: a mobile-first web app, installable as a PWA, before any native app.
2. **Payment links are now standard.** Rent Manager, Boyot and RentLynk let the tenant start the payment from a link in an SMS. We only let staff start an STK push.
3. **Water is a real business line.** Bomahut sells to water-billing companies, HomeManager builds the caretaker around meter readings, ShifTenant and Gate Africa go as far as prepaid meters. None of the global leaders handle it.
4. **Fraud with edited M-Pesa SMS is a named problem** (Silqu). A tenant or caretaker shows a fake or reused code and it is typed in as paid.
5. **Estates are served by separate products** (Gate Africa, Blocks), with access control and community features that rental tools do not have.
6. **Custody of money is a split.** Rent Manager, RentLynk, Porchplus and PayProp hold or route the money (payouts, wallets, escrow, interest, discounting). EzRent and we do not. Holding money needs licensing in Kenya (D-038, D-045 item 1).
7. **Global leaders compete on move-in/move-out records, bookkeeping capture and AI.** Condition reports protect deposits, receipt photos make bookkeeping easy, and AI drafts text or sorts repairs.
8. **Pricing models vary widely**: per transaction (Rent Manager), property count with every feature (RentLynk), unit tiers (EzRent, HomeManager), customer type (Pangoni), free with tenant fees (TurboTenant). Free tiers range from 3 to 5 units.

## 12. Tax change that affects the product [VERIFY with an accountant]

- **Non-resident landlords: a 10% final tax on gross rent** under the new section 6B, from 1 July 2026 (Finance Act 2026), filed monthly by the 20th, unless a registered Kenyan agent withholds it. This hits the diaspora segment directly and gives agents a withholding duty.
- **eRITS registration may become mandatory**: draft Residential Rental Income Tax Regulations 2026 would require landlords above KSh 288,000 a year to register on eRITS; non-residents register through a local agent.
- **Rental income tax agents** (since the Finance Act 2023) withhold 7.5% and remit within five working days.
- **For us**: the MRI estimate report (D-037) needs a resident or non-resident flag on the owner (7.5% or 10%), and agency mode needs a withholding statement per owner. Both stay estimates labelled "confirm with your tax adviser".

## 13. Ideas, and whether they fit Kenya

Viability: **Build** = fits Kenya and our design, build it; **Design-in** = add fields or interfaces now, build later; **Later** = worth doing after launch; **Skip** = wrong for us.

| # | Idea | Seen at | Viability | Why | Proposed placement |
|---|---|---|---|---|---|
| 1 | **Guard against paying the same M-Pesa code twice** (typed by hand and also sent by Safaricom) | Silqu | **Build — done 2026-09-29** | A hand-recorded code followed by the C2B callback credited the tenant twice | D-045 item 12 |
| 2 | Tenant **payment link**: a private link in the rent SMS opens a page where the tenant starts the STK push for any amount | Rent Manager, Boyot, RentLynk | **Build** | Removes typing the Paybill and reference, so fewer unmatched payments; reuses `StkRequest` | Next, before the tenant portal |
| 3 | **Pay in parts** ("Lipa Mdogo"): the link accepts any amount, and reminders show "KSh X of Y paid" | RentLynk | **Build** (without a wallet) | We already accept part payments and keep credit; the wallet would mean holding money | With item 2 |
| 4 | **Caretaker meter readings** on a phone: photo, timestamp, flags for negative or extreme usage, landlord approval before billing | HomeManager, Silqu, Bomahut | **Build** | Water disputes are a daily Kenyan pain and a sales point no global tool has | Move metered water from Phase 9 to right after Phase 7 |
| 5 | Water charge line shows previous and current reading, units used, rate, and a minimum charge; shared meters split equally, by weight or at a flat fee | Buniva guide | **Build** | Transparency ends disputes | With item 4 |
| 6 | **Move-in and move-out condition reports** with photos and an item register per unit | TurboTenant, Porchplus, DoorLoop | **Build** | Backs deposit deductions with evidence, which strengthens our deposit clearance lead | Tier 2, with the move-out statement |
| 7 | **Tenant good-standing letter**: a PDF of payment history from the ledger, for the tenant's next landlord or a loan | RentRedi (bureau reporting) | **Build** (letter only) | Tenants ask landlords for references; the ledger already holds the data. Reporting to Kenyan credit bureaus needs licensing and consent | Tier 2; bureau reporting Later |
| 8 | **Late fees or interest on arrears**, opt-in per organization with a grace period and a cap | Silqu, PayProp, all US apps | **Design-in** | Common ask, but legality and tenant goodwill need care **[VERIFY with counsel]**; default stays off | A rule on the organization; build after a legal check |
| 9 | **Quarterly and yearly billing** | Porchplus (a year in advance), Kenyan commercial leases | **Build** | `Lease.Frequency` has only MONTHLY today; commercial tenants often pay quarterly | Phase 7 or 8 |
| 10 | Resident or non-resident owner, MRI 7.5% or 10% in the tax estimate, and an agent withholding statement | Finance Act 2026, eRITS | **Design-in** | The law changed on 1 July 2026 **[VERIFY]** | Field now, report with D-037 |
| 11 | **Annual rental income pack**: gross rent by month, the tax estimate, receipts and expenses, for filing | Baselane, Landlord Studio | **Build** | A clear reason to pay each year | Phase 7 reports |
| 12 | **Expense receipt photos** captured on a phone | Landlord Studio, Baselane | **Build** | Cheap with the Expenses module | Phase 9 Expenses |
| 13 | Statement import for **Equity, KCB and Co-operative Bank** first | Bomahut, Silqu | **Build** | These banks come up again and again; confirms D-043 item 7 | Bank CSV import |
| 14 | Unverified-code check: a typed M-Pesa code that no Daraja callback or statement confirms within 24 hours is flagged | Silqu | **Later** | Needs the M-Pesa statement import first (D-045 item 10) | With statement import |
| 15 | Caretaker checks whether a unit is free or **reserved** before a viewing | HomeManager | **Design-in** | Needs a RESERVED unit status or a prospect hold | With Prospect/Viewing |
| 16 | Installable app (PWA) and an **offline queue for caretakers** | RentLynk (offline), everyone web-first | **Build** | Matches doc 16 §8.6; native apps later | Phase 7–8 |
| 17 | WhatsApp assistant answering "what is my balance?" | Nyumba Zetu, DoorLoop | **Later** | Webhook and consent are already in place | After the tenant portal |
| 18 | AI help: listing text, sorting repair requests, inspection summaries | TurboTenant, DoorLoop | **Later** | Useful, needs the permission-aware AI layer (doc 15) | Phase 9 |
| 19 | Estate features: guest codes, notice board, emergency contacts, domestic-staff register | Gate Africa, Silqu | **Later** | Separate segment; our ESTATE design-in covers the base | Estates module |
| 20 | Prepaid or smart meters and token sales | ShifTenant, Gate Africa | **Skip building; partner later** | Hardware and a vending licence; bill only postpaid meters and mark prepaid meters as "not billed" | Design-in `Meter.kind` |
| 21 | Listing syndication (Jiji, BuyRentKenya, Property24) | Pangoni, TurboTenant | **Later** | Our vacancy link and WhatsApp share cover the need first | After Prospect/Viewing |
| 22 | Referral earnings for agents or consultants (KSh 500 per listing, 30% of renewals) | Renters Hub, RentLynk | **Build** (as referral credit) | Word of mouth drives this market (§7 item 16) | Phase 8 growth |
| 23 | Pilot price locked for 12 months, founder-led onboarding | EzRent | **Build** (commercial) | Low cost, fits our import concierge | Doc 05 and pilots |
| 24 | Wallets, escrow, same-day payouts, interest on balances, rent discounting | RentLynk, Porchplus, PayProp, Rent Manager | **Skip** | Holding money breaks D-038 and needs licences | — |
| 25 | Multi-currency, short-stay, off-plan sales | Porchplus, OurProperty NG | **Skip** | Different business | — |

## 14. How we advance on them

1. **Fewest unmatched payments in the market.** The reference guard (done), a payment link that fills the reference for the tenant, STK push, phone suggestions, the daily reconciliation and, later, the unverified-code check. Measure the auto-match rate per account and show it on the dashboard (target in Phase 6: 90%).
2. **Water that ends arguments.** Caretaker readings with photo proof, anomaly flags, approval before billing, and a bill line the tenant can check. This beats Bomahut and HomeManager on trust, and no global tool offers it.
3. **Deposits with evidence.** Condition reports at move-in and move-out plus our deposit sub-ledger and clearance statement give a full, evidenced story. Competitors either escrow the money (a licence problem) or have nothing.
4. **Honest tax help after the 2026 changes.** Resident and non-resident rates, an annual pack, and agent withholding statements, all clearly labelled as estimates.
5. **Tenant goodwill as a feature.** Pay-in-parts, good-standing letters and no tenant fees (TurboTenant charges tenants; we don't).
6. **Price without surprises.** Every feature on every tier (RentLynk shows it sells), a free tier up to 3 units, and pilot prices locked for 12 months.

## Sources (Part 3)
[Rent Manager Kenya](https://rentmanager.co.ke/), [Silqu](https://silqu.com/), [RentLynk](https://rentlynk.co.ke/), [EzRent](https://www.ezrent.co.ke/), [Bomahut features](https://www.bomahut.com/features), [Pangoni](https://pangoni.io/), [Nyumba Zetu](https://www.nyumbazetu.com/), [HomeManager caretaker software](https://buniva.co.ke/blog/caretaker-software-kenya.html), [Buniva water billing guide](https://buniva.co.ke/blog/water-billing-rental-property-kenya.html), [ShifTenant IoT billing](https://shiftenant.com/blogs/how-shiftenant-uses-iot-to-reinvent-utility-billing-for-landlords-and-tenants), [Renters Hub](https://rentershub.co.ke/), [Boyot Kenya](https://kenya.boyot.app/), [PayProp](https://www.payprop.com/za), [PayProp interest on arrears](https://www.payprop.com/us/blog/payprop-automates-agents-billing-of-interest-on-rent-arrears-to-protect-landlord-cash-flow-za), [Gate Africa](https://gate.africa/), [OurProperty NG](https://ourproperty.ng/), [Porchplus](https://www.porchplus.com/), [RentRedi](https://rentredi.com/), [Baselane](https://www.baselane.com/), [Landlord Studio](https://www.landlordstudio.com/), [DoorLoop](https://www.doorloop.com/), [TurboTenant](https://www.turbotenant.com/), [Buildium features](https://www.buildium.com/features/), [KRA rental income](https://www.kra.go.ke/individual/filing-paying/types-of-taxes/residential-rental-income), [Pangoni: eRITS](https://pangoni.io/blog/compliance/kra-erits-landlords-kenya), [Manwa Advocates: Finance Act 2026 and non-resident rent](https://manwaadvocates.com/non-resident-rental-income-tax-what-the-finance-act-2026-means-for-diaspora-property-owners/), [NTV: mandatory landlord registration](https://ntvkenya.co.ke/news/residential-landlords-face-mandatory-registration-in-kra-rental-tax-drive/)
