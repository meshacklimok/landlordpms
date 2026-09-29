# 08 — Decisions Log

Format: **ID — Title — Status — Date**. Context, decision, consequences. Add new entries at the bottom. Never edit history; supersede with a new entry.

---

### D-001 — Project name is landlordpms — ACCEPTED — 2026-09-26
"RentFlow Kenya" was a working name and is retired. All docs and code use **landlordpms**.

### D-002 — Django with separate apps per domain — ACCEPTED (pre-existing)
19 apps scaffolded. Only the MVP apps get code first; the rest stay empty until needed.

### D-003 — Custom User model before first migration — PROPOSED
Login by phone or email. Set `AUTH_USER_MODEL` before running any migration; delete the existing scratch `db.sqlite3`.

### D-004 — Multi-tenancy: shared database, `organization` FK on every business table — PROPOSED
Scoped manager + view mixin + mandatory isolation tests. Rejected: schema-per-tenant (operational cost) and database-per-tenant.

### D-005 — Money as Decimal, KES, ledger-based balances — PROPOSED
No floats. Append-only ledger. No deletes on financial data; reversals instead.

### D-006 — PostgreSQL from now on — ACCEPTED (database created and migrated 2026-09-26)
Replaces "SQLite temporarily". Reason: concurrency, decimals, and payment idempotency behave differently in SQLite.

### D-007 — Time zone Africa/Nairobi — PROPOSED

### D-008 — M-Pesa collection strategy — ACCEPTED (own Paybill/Till via Daraja C2B, see D-045)
MVP: record-only/manual. Next: landlord's own Paybill/Till via Daraja C2B. Aggregator model deferred pending legal advice. See 01 §C.

### D-009 — Business logic in services.py — PROPOSED
Views/API/jobs call services; keeps rules testable and reusable.

### D-010 — Frontend: Django templates + Bootstrap 5 + HTMX, delivered as a PWA — PROPOSED
Mobile-first, low bandwidth. A REST API is added later for native apps.

### D-011 — Background jobs via Celery + Redis — PROPOSED

### D-012 — Start with residential rentals only — PROPOSED

### D-013 — Free tier of ~5 units — PROPOSED (validate in pilots)

### D-014 — User, Organization, Membership and Role are separate concepts — PROPOSED
No `is_landlord` flag. Membership carries the role and property scope. Platform Admin is `is_staff`/`is_superuser`, outside organizations.

### D-015 — Roles and capabilities live in the database, editable in admin — PROPOSED
Capabilities are defined in code; roles bundle them. System roles seeded; custom per-organization roles are designed for, built later.

### D-016 — Lease can have multiple tenants (LeaseTenant); tenants can have many leases — PROPOSED
No overlapping active leases on one unit. Transfer = end lease, new lease with `previous_lease`.

### D-017 — Property has exactly one managing organization — PROPOSED
Joint ownership and agency client owners are added later through an optional owner link, not by sharing properties.

### D-018 — Occupancy is derived from leases; rent history is records not edits — PROPOSED

### D-019 — Payment accounts are their own entity, many per organization, many-to-many with properties — PROPOSED
Units carry a `payment_reference`. M-Pesa transactions attach to a payment account, not the landlord. Unmatched payments go to an inbox, never guessed.

### D-020 — Billing (owed) is separate from payments (paid); ledger plus allocations — PROPOSED

### D-021 — Subscription billing (landlord pays us) is a separate app from tenant billing — PROPOSED

### D-022 — No models or views until docs 10–12 are approved — ACCEPTED

### D-023 — Roles are per-organization and fully editable; templates are copied at organization creation — ACCEPTED
Supersedes the "custom roles later" part of D-015. Each organization can rename, add, clone and edit roles. Owner role is protected and at least one Owner must exist. See doc 13.

### D-024 — Per-person capability overrides (MembershipCapability); no privilege escalation — ACCEPTED
You can only grant capabilities you hold yourself. All role and grant changes are audited.

### D-025 — Caretaker cash payments: allowed only if the Owner/Manager grants `payments.record`; `payments.confirm` decides whether it needs review — ACCEPTED
Default caretaker template does not include it. Resolves TODO decision 1.

### D-026 — Added default templates Leasing/Letting Agent and Maintenance Manager — ACCEPTED

### D-027 — Three separate money flows: platform billing, property financial records, property expenses — ACCEPTED
No shared tables, sequences or reports. Subscriptions never touch a landlord's tenant ledger; expenses never change tenant balances. Doc 11 §22.

### D-028 — Archive vs never-delete matrix — ACCEPTED
Financial and audit records are never deleted; business records are archived; only unissued drafts and temporary tokens are hard-deleted; personal data is anonymised after retention. Doc 11 §23.

### D-029 — Locked per-organization number sequences assigned at issue; tenant-facing reference `{PROPERTY_CODE}-{UNIT_CODE}` — ACCEPTED
Platform documents use their own `LPM-` sequences. Doc 11 §24.

### D-030 — Money: Decimal(14,2), KES, line-level ROUND_HALF_UP, single money helper, string serialisation — ACCEPTED
Doc 11 §25.

### D-031 — Report metrics are defined once in `reports/metrics.py` (definitions in doc 11 §26) — ACCEPTED

### D-032 — Notification delivery = organization rule AND recipient preference AND consent AND channel availability AND quiet hours; staff recipients chosen by capability — ACCEPTED
Doc 11 §27.

### D-033 — Group A recommendations A1–A13 accepted (doc 14) — ACCEPTED
Deposits are an isolated sub-ledger (DEPOSIT_RECEIVED/DEDUCTION/REFUNDED/TRANSFERRED, each with deposit_type, reason and optional proof, plus a Deposit Clearance Statement); opening balances plus CSV import; rent in advance with per-lease due day and grace; extra payer phones per lease and COMPANY tenant type; locked sequences; DB constraints and row locks; organization in session with UUID URLs; phone-first login with SMS OTP; lapse leads to read-only, never deletion; SMS wallet.

### D-034 — Tax is design-in only — ACCEPTED
Fields exist (`ChargeType.is_taxable`, nullable `tax_rate`, `Invoice.subtotal/tax_total/total`, `Organization.kra_pin/vat_registered`) but no tax calculation or eTIMS work until confirmed with an accountant.

### D-035 — Agency commission is design-in only — ACCEPTED
Nullable `Property.owner` and `Property.management_fee_percent`; owner statements and remittances come later.

### D-036 — Remaining Phase 0 decisions — ACCEPTED (recommended defaults)
- Agency/client-owner mode: design-in only, not in v1 (see D-035).
- Unit payment reference: `{PROPERTY_CODE}-{UNIT_CODE}`, e.g. `GV-A102` (doc 11 §24).
- Proration: by actual days, organization can switch to a full month (doc 14 A3).
- Grace days: per lease, default 0 to 3. Late fees are off in the MVP.
- Retention for former tenants: 90 days after offboarding for organizations; tenant personal data retention period **[VERIFY with counsel]** before launch.
- Phone-only login (no email) is allowed, with SMS OTP (doc 14 A11).
- Default payment allocation: oldest invoice first, manager override with audit.

### D-037 — MRI reporting and eTIMS are staged and adapter-based — ACCEPTED
Now: design-in fields only (D-034). Later: MRI estimate report using a configurable dated rate (currently 7.5% **[VERIFY]**), then an eTIMS adapter. No hard-coded tax rate; no integration until KRA requirements are confirmed.

### D-038 — Foundations D1–D15 in doc 14 Group D accepted — ACCEPTED
Key points: single-entry append-only ledger shaped for later double-entry; we never hold customer money and landlords use their own Paybill/Till/bank; "tenant" means renter and the customer is Organization; renters are per-organization records linked to a global User only by phone; application-level isolation now with row-level security considered later; effective dating on rent, charges, roles and account assignments; structured Kenyan addresses; legal review before pilots; organization owns and can export its data; RPO/RTO defined and restore tested before real money; business rules in services; per-organization settings stored as data with feature flags and entitlements; consented and logged impersonation with rate limits; unit economics before pricing.

### D-039 — Competitor benchmark findings adopted (doc 16) — ACCEPTED
1. eTIMS/eRITS moves from "later" to "confirm before launch": accountant check in Phase 0, `EtimsAdapter` interface designed in, and a go/no-go on eTIMS submission before public launch.
2. `MpesaTransaction` stores `msisdn_raw` and `msisdn_hash`; phone matching must work with hashed numbers **[VERIFY in Daraja sandbox]**.
3. The unallocated inbox is a daily first-class screen, plus an SMS asking the payer for their unit reference.
4. Add a free tier for tiny landlords and annual discounts; no hidden per-transaction fees.
5. Consider tenant portal, WhatsApp and metered water earlier than Phase 9.

### D-040 — Additions from competitor deep dive (doc 16 §7–9) — ACCEPTED
MVP: vacancy share link, P&L and cash flow by property, bank as a payment method with statement import, import concierge, optional MFA for Owner/Accountant, status and trust pages, VAT field on platform invoices. Tier 2: owner viewer and statements, lease PDF and move-out statement, prospects and viewings, vendor SLA, segmented announcements, WhatsApp, collectability score. Design-in only: BED_SPACE and semester billing, ESTATE and committee role, branch/white-label. Skip: short-stay, credit screening, public marketplace.

### D-041 — Phase 1 implementation choices — ACCEPTED (2026-09-26)
1. `PropertyAccess` ships in Phase 1, so a minimal `Property` (organization, name, code, category) ships with it. Phase 2 extends that model; it does not replace it.
2. Organization scoping is explicit: `Model.objects.for_org(org)`, which raises on `None`. There is no thread-local "current organization" and no automatic filtering.
3. `Organization.status` is ACTIVE, READ_ONLY (subscription lapsed: only capabilities marked `read_only_safe` work, and `subscription.manage` is one of them) or FROZEN (platform action: nothing works).
4. Capabilities are defined in code (`accounts/capabilities.py`) and synced after every migrate. Removed codenames are deactivated, never deleted. Existing role templates are not overwritten, except that the Owner template always gains new capabilities.
5. Org-wide capabilities (staff, roles, subscription, organization, payment accounts, full audit) also need `Membership.all_properties`. That is why property-scoped staff can never manage staff.
6. The database is PostgreSQL in every environment, including tests.

### D-042 — Phase 3 billing design — ACCEPTED (2026-09-27, open to change before merge)
1. **Signed ledger.** `LedgerEntry.amount` is signed: positive means the tenant owes more, negative is a credit, and the balance is the sum. Check constraints fix the sign per kind (INVOICE and PAYMENT_REVERSAL positive; INVOICE_VOID, PAYMENT and DEPOSIT_APPLIED negative; never zero). Rows are append-only in code; corrections are REVERSAL entries that point at what they cancel.
2. **Deposits are a separate table** (`DepositEntry`), not ledger kinds, so no query can mix them into rent. Held = sum per lease and deposit type. Deducting towards arrears writes a linked `DEPOSIT_APPLIED` credit to the rent ledger. Renewal and transfer activation move what is held as a linked pair of DEPOSIT_TRANSFERRED entries.
3. **Generated invoices are issued at once**: numbered INV-YYYY-NNNNNN (year of issue) and posted to the ledger. One invoice per lease per run holds rent and recurring charges.
4. **Idempotency lives on the line**: `(lease, billing_month, charge_type)` is unique among lines that are not void. A charge added mid-month is billed on a small extra invoice; voiding frees the month to be billed again. The lease row is locked while billing it.
5. **Proration**: one line per charge type per month. A mid-month rent change is summed exactly across its pieces and rounded once. With `prorate_partial_months` off, a partial month bills the full rate of the first billed day; a charge starting mid-month then starts next month.
6. **Timing**: the daily job bills the current month and, from `invoice_lead_days` (default 5) before it, the next month. Due date = the lease's due day in the billed month, but never before move-in or the issue date. `overdue_after` = due date + grace days, stored on the invoice.
7. **No automatic catch-up** of earlier months: those are covered by an opening balance (one live `OPENING_BALANCE` per lease, changed by reversal) or billed by hand for a chosen month.
8. **New capabilities**: `deposits.record` (Owner, Manager, Accountant) and `deposits.deduct` (sensitive; Owner, Manager). Opening balances need `invoices.adjust`; voiding needs `invoices.void` and no payments allocated.
9. **Invoices follow later lease changes.** Ending a lease, a rent change or moving a charge's end re-checks every month already billed from that day: a month whose lines no longer match is voided and billed again with its original due date. Invoices with payments are left for manual correction. A charge added from a month already billed is billed at once on its own invoice.

### D-043 — Phase 4 payments design — ACCEPTED (2026-09-27, open to change before merge)
1. **One ledger entry per payment, not per allocation.** A `Payment` gets one `LedgerEntry.PAYMENT` (posted on confirm) and, if reversed, one `PAYMENT_REVERSAL`, both linking back through a new nullable `LedgerEntry.payment` FK (same shape as the existing `invoice` FK). `PaymentAllocation` rows are bookkeeping for which invoices a payment covers (drives `Invoice.amount_paid`/status and the receipt's line list); they do not drive the lease balance, so an unallocated remainder is not a second ledger entry — it is simply a payment with `amount` greater than the sum of its allocations, swept into invoices later.
2. **Maker/checker by capability, not role** (doc 13): `payments.record` creates a `Payment`; if the actor also holds `payments.confirm` it is confirmed in the same call (allocated, posted, receipted). Otherwise it sits `PENDING_REVIEW`, posts nothing, and waits in a review queue for a `payments.confirm` holder to confirm or reject.
3. **Default allocation is oldest open invoice first.** `payments.allocate` can override with an explicit invoice/amount list at confirm time. A separate "apply credit" action, also `payments.allocate`, sweeps a payment's unallocated remainder into invoices that are open now — used after new invoices are issued.
4. **Reversal only from `CONFIRMED`** (`payments.reverse`, sensitive, reason required): unwinds each `PaymentAllocation` (`Invoice.amount_paid` and status roll back), writes one `PAYMENT_REVERSAL` for the full amount, sets `Payment.status = REVERSED`. Rejecting a still-`PENDING_REVIEW` payment (`payments.confirm`) just sets it to `REVERSED` directly, since nothing was posted yet.
5. **Receipts are numbered and rendered to PDF at confirm time** (`RCT-YYYY-NNNNNN`, ReportLab — pure Python, no system dependencies) and kept even if the payment is later reversed; nothing here is ever hard-deleted (D-028).
6. **`PaymentAccount` ships without `credentials`.** The M-Pesa columns are Phase 6's concern; this slice only needs `type`, `number`, `display_name` and the usual archive lifecycle.
7. **Scope for this slice**: `PaymentAccount`/`PropertyPaymentAccount`, `Payment`/`PaymentAllocation`, reversal, receipts. Bank-statement CSV import and the P&L/cash-flow/aged-receivables reports are deferred to a follow-up slice.

### D-044 — Phase 5 communications design — ACCEPTED (2026-09-28, open to change before merge)
1. **The notification type catalog lives in code** (`notifications/catalog.py`), like capabilities: codename, audience (tenant or staff), default channels, default on/off, mandatory, urgent, reminder offsets and the placeholders its templates may use. Rows store the codename; there is no catalog table.
2. **`OrganizationNotificationRule` holds only changes.** No row means the catalog default. A row stores enabled, channels in order, reminder offsets in days and whether co-tenants also receive it. Mandatory types cannot be switched off.
3. **`NotificationPreference` holds opt-outs** per recipient (tenant or staff user), per type (or all types) and per channel. **`ConsentRecord`** logs grants and revocations per recipient and channel. For service messages about a tenancy (invoices, reminders, receipts, property notices), consent to SMS is implied by the lease; an explicit revocation (a STOP reply, or staff recording it) blocks the channel for every non-mandatory type. WhatsApp needs an explicit grant. There are no marketing messages. **[VERIFY the implied-consent basis with counsel before pilots.]**
4. **`Message` is the log of every send, skip and failure** (doc 11 §27), never deleted. One row per recipient per event: the delivery engine tries the rule's channels in order and uses the first one allowed; if none is allowed, it writes one SKIPPED row with the reason. `dedupe_key` is unique per organization, so a trigger or a daily job that runs twice sends nothing twice.
5. **Delivery check** = rule enabled AND not opted out AND consent AND channel available AND address known. Quiet hours (organization setting, default 21:00–07:00 Africa/Nairobi) do not skip a message: non-urgent messages wait in QUEUED until quiet hours end.
6. **Sending runs after commit**, then again from `send_due_messages` (run every few minutes by cron) for delayed and failed messages. A failed send is retried up to 3 times with backoff, then marked FAILED. No Celery until the volume needs it.
7. **Templates**: default bodies are in code for each type, channel and language (English and Swahili, Swahili **[VERIFY with a native speaker]**). A `MessageTemplate` row is an organization's override, archivable. Bodies use `{placeholder}` fields only, filled by a safe formatter, never the Django template engine, so staff-edited text cannot run code. Unknown placeholders are refused when saving.
8. **Recipient language**: new `Tenant.language` (en or sw, default en). Staff get English until user settings exist.
9. **In-app** is a `Message` on the IN_APP channel to a staff user, with `read_at`, shown under a bell in the header. Tenants have no in-app channel until the tenant portal (Phase 7).
10. **SMS wallet**: `channels.sms_available(org)` returns True until platform billing (Phase 8) adds the wallet. Provider cost is still stored on each message.
11. **Build order**: (1) models, catalog, delivery engine, message log, dispatch; (2) triggers: invoice issued (not on rebill re-issues), rent due soon, rent overdue, payment received with a receipt link, and the daily reminders job; (3) pages: message log, notification settings, templates, the bell, and tenant opt-out; (4) Africa's Talking adapter, delivery reports and STOP replies; (5) segmented announcements; (6) WhatsApp adapter.
12. **Step 2 details** (2026-09-28): invoice issued fires from the monthly run (job or Generate button) only, never from `rebill_from` or `bill_missing`. Bulk triggers queue messages for `send_due_messages` instead of sending inline. Due-soon offsets count days before the due date; the nearest reached offset is sent, and none if the invoice was issued on or after that day. Overdue offsets count days after `overdue_after` (1 = first overdue day); a reminder missed by the job is sent up to 2 days late, then dropped. Reminders run inside `billing_daily`. Payment received goes to the payer first; the receipt link is a random `Receipt.share_token` served at `/r/<token>/` without login (rate-limited, noindex) and stops working when the payment is reversed. Absolute links use the new `SITE_URL` setting (required in production). Payment waiting for review is in-app to members who may confirm it for that property. SMS dates are numeric (dd/mm/yyyy) so they read the same in both languages; a balance in credit shows as zero.
13. **Step 3 details** (2026-09-28): pages live under `/messages/`. The log shows tenant messages only (staff in-app messages belong to the bell) and is scoped to the tenants the member can see; failed messages can be sent again with `messages.send`, which resets the attempts. Settings (`organization.manage`) edit on/off, reminder days (at most 3, 0–60, overdue from 1) and co-tenants per type, plus quiet hours; a rule equal to the default is deleted, not stored. Templates (`templates.manage`) archive the previous wording on every save, and saving the default text removes the override. Staff stop or resume SMS for a tenant from the tenant page (`tenants.manage`); this writes a STAFF `ConsentRecord`, and required messages still go. The nav bell counts unread in-app messages, and opening the inbox marks the shown ones read.
14. **Step 4 details** (2026-09-28): `core.sms.AfricasTalkingSmsSender` posts to the classic `version1/messaging` form API with `urllib` (no new dependency); the username `sandbox` switches to the sandbox URL. Recipient codes 100–102 count as sent and the cost is stored; codes 403, 404, 406 and 409 (invalid number, unsupported number, blacklisted, do-not-disturb) and HTTP 4xx fail at once without retries. Africa's Talking does not sign its callbacks, so the delivery-report, inbound-SMS and bulk opt-out callbacks live under a secret URL, `/hooks/sms/africastalking/<AT_CALLBACK_TOKEN>/<delivery|inbound|optout>/`, and return 404 while the token is unset. `Success` marks a sent message DELIVERED; `Failed`, `Rejected`, `AbsentSubscriber` and `Expired` mark it FAILED; in-flight statuses change nothing. A shared sender ID cannot tell organizations apart, so a STOP reply (STOP, UNSUBSCRIBE, ACHA, SIMAMA and similar) or a network opt-out revokes SMS for every tenant record with that phone, in every organization, and START (or ANZA) restores it; each change is a SMS_REPLY `ConsentRecord` and an audit event. **[VERIFY the API fields, status codes and callback fields in the Africa's Talking sandbox.]**
15. **Step 5 details** (2026-09-28): an `Announcement` row records the text, who sent it, a plain-language summary of the audience and the recipient count; each recipient gets one `announcement` `Message` linked to it (dedupe key per announcement and tenant, so a double submit sends nothing twice; the form also carries a one-time id). The audience is the tenants on leases occupying a unit today in the properties the member can see (`messages.send_bulk`), narrowed by any of: properties, buildings, owing money past its due date for at least N days (only offered with `invoices.view`), and lease contract ending within N days. The primary tenant always, co-tenants unless unticked; a tenant on two matching leases gets one message. The text may use `{tenant_name}`, `{unit}` and `{property}`, filled per recipient before the organization name is added. Sending is two steps: a preview shows who will receive it, who will be skipped and why (the same checks as sending), the SMS parts per message and an estimated cost (`SMS_PRICE_ESTIMATE`, KES per part, default 0.80, **[VERIFY against the Africa's Talking price list]**), then Send. Small batches (25 or fewer) send at once, larger ones through `send_due_messages`. Announcements wait for quiet hours unless "send now, even in quiet hours" is ticked. Members with every property see all announcements; scoped members see the ones they sent. Saved segments are deferred.
16. **Step 6 details** (2026-09-28): WhatsApp goes through the **Meta WhatsApp Cloud API** directly (`core.whatsapp.CloudApiWhatsAppSender`, `urllib`, Graph API version in `WA_API_VERSION`), from one platform business number shared by every organization, like the SMS sender ID; per-organization numbers are deferred. `WHATSAPP_BACKEND` unset means the channel is unavailable and tenants get SMS.
    - **Only approved templates.** Messages we start must use a template Meta approved, so the WhatsApp wording is fixed in code for each tenant type (English and Swahili) and organizations cannot edit it (the templates page shows it read-only). The approved template is named `<type>_v1` in `en` or `sw`; its parameters are the `{field}` values in the order they appear. `manage.py whatsapp_templates` prints the text to submit to Meta (category Utility, fields as `{{1}}`, `{{2}}`…). Parameter values are flattened to one line because Meta refuses new lines in parameters, and an empty value is sent as "-". Each message stores its template name, language and parameters, so a retry sends exactly the same. Each WhatsApp body is the SMS wording with "Message from {org_name}." first and "Reply STOP to stop WhatsApp messages." last, since Meta refuses templates that start or end with a field. The announcement template (free `{text}`) may be classed as marketing by Meta **[VERIFY at submission]**.
    - **WhatsApp first, then SMS.** Tenant types default to WhatsApp then SMS: a tenant who agreed to WhatsApp gets it, everyone else gets SMS as before. When nothing can be sent, the log shows the first reason that is not "channel not available", so a tenant who stopped SMS shows "No consent", not "WhatsApp not available".
    - **Consent is an explicit grant** (item 3): staff record it on the tenant page with a note of how the tenant agreed **[VERIFY what record counsel needs]**. A STOP-type WhatsApp reply revokes WhatsApp for every tenant with that phone in every organization, and START restores it only for tenants who had agreed before (a first grant never comes from a reply, because the shared number cannot tell which landlord the tenant means).
    - **Webhook** at `/hooks/whatsapp/`: GET answers Meta's verification with `WA_VERIFY_TOKEN`; POST must carry a valid `X-Hub-Signature-256` (HMAC-SHA256 of the raw body with `WA_APP_SECRET`) or gets 403. Status `delivered` or `read` marks a message DELIVERED, `failed` marks it FAILED with Meta's code and title, `sent` changes nothing. Meta does not report a price per message, so WhatsApp cost stays blank and the announcement estimate counts SMS only.
    - **Errors**: HTTP 429, 5xx and Meta's throttling codes (4, 80007, 130429, 131048, 131056) are retried; any other 4xx fails at once (for example 131026 not on WhatsApp, 132001 template missing, 190 expired token). **[VERIFY codes against the Cloud API error reference.]** Production refuses to start with the Cloud API backend unless `WA_PHONE_NUMBER_ID`, `WA_ACCESS_TOKEN`, `WA_APP_SECRET` and `WA_VERIFY_TOKEN` are set.
    - Deferred: per-organization numbers, WhatsApp prices in estimates, opt-in from a tenant's own message, free replies inside Meta's 24-hour window, and a conversation inbox.

### D-045 — Phase 6 M-Pesa design — ACCEPTED (2026-09-28, open to change before merge)
1. **Each organization collects on its own Paybill or Till through its own Daraja app (C2B).** We never hold tenants' money. This settles D-008 for now; the aggregator model stays deferred pending legal advice.
2. **Credentials live in `mpesa.DarajaCredentials`**, one row per M-Pesa payment account (Paybill or Till): environment (sandbox or production), shortcode, consumer key, consumer secret and the STK passkey. The three secrets are encrypted at rest with `cryptography` Fernet (`core/crypto.py`, MultiFernet over `FIELD_ENCRYPTION_KEYS`, comma-separated, the first key encrypts, so keys can be rotated). They are never sent back to the browser: the settings form shows whether each is set, and a blank field keeps the stored value. Editing them needs `mpesa.settings`; the audit log records which fields changed, never their values. Production refuses to start without `FIELD_ENCRYPTION_KEYS`.
3. **Callbacks carry an unguessable per-account token** (`/hooks/c2b/<token>/confirm/`, `/validate/`, `/stk/`; Daraja refuses callback URLs containing words such as "mpesa", "safaricom", "exe", "cmd", "sql" or "query", so tokens that contain one are regenerated and a SITE_URL with one is refused at registration), generated when credentials are first saved and replaceable. An unknown token gets 404. Validation always accepts (Safaricom enables it only on request, so we cannot rely on rejecting a wrong reference at payment time) **[VERIFY]**. An optional `MPESA_ALLOWED_IPS` restricts callbacks to Safaricom's addresses **[VERIFY the current list]**.
4. **`MpesaTransaction` stores the raw payload first, then processes it.** `trans_id` is unique, so a repeated callback changes nothing and is still acknowledged. Amounts are parsed with `Decimal`. `TransTime` is read as Africa/Nairobi. A shortcode that does not match the account is stored as FLAGGED and never matched. Nothing is deleted: a transaction that is not rent is IGNORED with a reason.
5. **Phones may arrive hashed.** Daraja may send a SHA-256 hash in `MSISDN` instead of the number. We keep `msisdn_raw` as received and `msisdn_hash`: the value itself when it is 64 hex characters, otherwise the SHA-256 of the number as `2547XXXXXXXX` **[VERIFY the exact hash input in the sandbox]**. Phone matching hashes the tenants' and extra payers' (`LeasePayer`) numbers the same way. A payer can only be texted when the real number arrived.
6. **Matching, in order** (doc 11 §10):
    - **Reference.** The account reference, ignoring case, spaces and dashes, equals a unit's `payment_reference` in the account's organization; the unit has exactly one lease in force; and the account serves that unit's property (or serves no property in particular). This counts as **confident**: the payment is confirmed at once — posted, allocated oldest invoice first, receipted and the tenant notified — with no reviewer (Safaricom has already confirmed the money). Recorded as `payments.record_system_payment`, with `confirmed_by` empty and the source in the audit log.
    - **Phone.** The payer's number (or hash) belongs to a tenant or extra payer on exactly one lease in force in the organization. If several leases match, the one whose balance due equals the amount wins. This is only a **suggestion**.
    - **Otherwise** the transaction is UNMATCHED and waits in the inbox.
7. **The unallocated inbox is a daily screen** (`mpesa.match`, scoped to the properties the member can see; transactions on an account that serves no property need an organization-wide member). The actions are: accept the suggestion, match to any lease the member can see, or ignore with a reason. A match creates a confirmed payment exactly as in item 6. Reversing that payment later (`payments.reverse`) puts the transaction back in the inbox. Holders of `mpesa.match` get an in-app alert for each unmatched payment. When the real number is known, the payer gets one SMS saying the payment was received but not matched and asking for the unit reference.
8. **STK push** (Lipa na M-Pesa Online) on Paybill accounts that have a passkey: staff with `payments.record` send a request to a tenant's phone from the lease page. An `StkRequest` row tracks it. The STK callback creates the `MpesaTransaction` from `MpesaReceiptNumber` and matches it to that lease with confidence. A C2B confirmation for the same receipt is a duplicate and is ignored **[VERIFY whether Daraja sends both]**.
9. **Daraja client** (`mpesa/daraja.py`) is an adapter like SMS: OAuth token cached per account for less than its lifetime, register C2B URLs (response type `Completed`), STK push and STK query; `urllib`, no SDK. `MPESA_CLIENT` selects it; a fake client is used in tests.
10. **Daily job** `mpesa_daily`: retries transactions stuck in RECEIVED, queries STK requests still pending after 10 minutes, and writes a reconciliation summary per account for the day before (count and total received, auto-matched, matched by hand, ignored, still waiting). The summary goes to organization-wide `mpesa.view_transactions` holders in-app. Importing the M-Pesa statement CSV to catch missed callbacks (and Till accounts without Daraja) is deferred with the bank CSV import (D-043 item 7).
11. **Build order**: (1) encryption, credentials, Daraja client, the account's M-Pesa settings page and URL registration; (2) callbacks, `MpesaTransaction`, the matching engine and system confirmation; (3) the inbox, transaction list, manual match and ignore, alerts and the payer SMS; (4) STK push; (5) the daily job, the reconciliation summary and the go-live checklist.
12. **One M-Pesa code is counted once** (2026-09-29, from doc 16 Part 3). If a live payment (not reversed) in the organization already carries the transaction's code as its reference, typed in by hand, for example from the tenant's SMS, then the transaction is never paid automatically. It waits in the inbox as UNMATCHED, with the typed payment's lease as the suggestion and a note saying to check it and ignore this one. Staff get the in-app alert; the payer gets no "could not match" SMS, because they already have a receipt. Matching it by hand or accepting the suggestion is refused while the typed payment is live. After that payment is reversed, the transaction can be matched normally. The other way round: recording an M-Pesa payment by hand with a code that is waiting in the inbox (RECEIVED, UNMATCHED or FLAGGED) is refused, and "Record it anyway" does not override it; staff match it from the inbox instead.

### D-046 — Third competitor pass: what to build next — APPROVED (2026-09-29)
From doc 16 Part 3 (21 websites and apps). Item 12 of D-045 is already built. The owner approved the list as proposed on 2026-09-29.
1. **Next (after Phase 6 is merged):** a tenant **payment link**, a private link in rent SMS where the tenant starts an STK push for any amount (pay in parts, no wallet). Needs `StkRequest.requested_by` to allow empty (the tenant starts it), limits per link and phone, and an agreed rule for which phones may be used (any Kenyan number, or only the lease's tenants and payers).
   **Built 2026-09-29 (`mpesa/paylinks.py`):**
   - One `PayLink` per lease with a private token, `/p/<token>/`. It is made the first time a rent message needs it, and only when a Paybill with a passkey collects for the property. Staff with `payments.record` can make a new link (the old one stops at once), turn it off, or turn it on, from the lease's "Request M-Pesa payment" page.
   - **Phone rule (decided): any Kenyan number.** Relatives and employers often pay. The prompt still needs the PIN of whoever owns that phone, so the risk is nuisance prompts, not lost money.
   - **Limits:** 3 prompts per link in 10 minutes, 10 per link a day, 5 a day to one phone from any of the organization's links, 30 page loads or sends per IP an hour, plus the existing 2-minute wait per lease and phone.
   - `StkRequest.requested_by` may be empty; `StkRequest.pay_link` records the link. One of the two must be set (constraint).
   - The link works while the lease is ACTIVE, or ENDED/TERMINATED with money still owed; never for a draft, archived or renewed lease, or an organization that is not operational.
   - The page shows the organization, property, unit, amount owed and the Paybill with the account number as a fallback. It never shows tenant names or phones. It is `noindex` and sends no referrer.
   - `{pay_link}` is in the default SMS wording of invoice issued, rent due soon and rent overdue. It is empty when the lease has no working link, and the text reads as before. It is **not** in the WhatsApp wording, so the `_v1` templates for Meta are unchanged. The link adds about 45 characters and can push a message into a second SMS segment.
   - A payment for an ended lease follows D-045: it waits in the M-Pesa inbox for someone to match it.
2. **Move metered water forward** to right after Phase 7: caretaker readings with photo and timestamp, flags for negative or extreme usage, approval before billing, bill lines showing the readings, a minimum charge, and shared-meter splits (equal, weighted, flat). Prepaid meters are marked "not billed".
3. **Tier 2 additions:** move-in and move-out condition reports with photos and an item register per unit (feeding deposit deductions); a tenant good-standing letter (PDF from the ledger); quarterly and yearly billing; the annual rental income pack.
4. **Design-in now:** owner resident or non-resident flag for the tax estimate (7.5% or 10% after the Finance Act 2026 **[VERIFY]**); opt-in late fees with grace and cap (default off, **[VERIFY with counsel]**); a RESERVED hold on units; `Meter.kind` (postpaid or prepaid).
5. **Commercial:** every feature on every tier, a free tier up to 3 units, pilot prices locked for 12 months, and referral credit (doc 05).
6. **Skip:** wallets, escrow, payouts, interest on balances, rent discounting (they break D-038), multi-currency, short-stay and off-plan sales.

### D-047 — Move-in and move-out condition reports — ACCEPTED (2026-09-29, open to change before merge)
D-046 item 3, first part. Built on `feature/condition-reports`, cut from `feature/phase6-mpesa`.
1. **A new `inspections` app.** Each unit has an **item register** (`UnitItem`): area (e.g. Kitchen), item, quantity and a note, archivable. The first report on a unit with an empty register fills it from a default list in code, chosen by unit type (rooms and fittings for homes, doors, shutters and fittings for shops and offices). Staff with `inspections.record` add, rename and archive items on the unit's register page.
2. **One report per lease per kind** (`ConditionReport`, MOVE_IN or MOVE_OUT), unless the earlier one was cancelled. Reports are for issued leases only: a draft can be deleted, and the evidence must not go with it. Move-in needs an ACTIVE lease; move-out an ACTIVE, ENDED or TERMINATED one. A RENEWED lease gets neither, because the tenant stays.
3. **DRAFT → COMPLETED, or CANCELLED.** Starting a report copies the unit's register into its lines (area, item and quantity as they were that day). A draft is edited freely: each line gets a condition (Good, Fair, Poor, Damaged, Missing or Not checked) and a note; items found on the day are added to the register and the report. **Completing** needs every line assessed ("Not checked" is an answer; blank is not) and then locks the report: nothing changes after that. A mistaken report is cancelled with a reason and kept; a new one can then be started.
4. **Photos** belong to a line or to the report in general. The upload is checked and re-encoded by Pillow: EXIF orientation applied, then saved as JPEG at most 1600 px on the long side, which strips EXIF data such as the phone's location. At most 10 MB per upload, 10 photos per line and 60 per report. File names are random, under `condition-reports/<year>/`, and photos are only served through a view that checks the member may see the report, never from `/media/`. Each photo keeps who uploaded it and when. Photos can be removed while the report is a draft only.
5. **On the day**: who inspected it, the date (not in the future), whether a tenant was present, the tenant's comments, keys handed over (number), and general notes.
6. **Move-out compares with move-in.** The move-out report shows, per item, the condition and photos from the latest completed move-in report of this lease, or of the lease it renewed on the same unit, and marks items that are worse (Good < Fair < Poor < Damaged < Missing; "Not checked" is not compared).
7. **Deposit deductions cite the report.** A completed move-out report offers "Deduct from deposit" to members with `deposits.deduct`, with the reason filled from the items that are worse. It runs the normal `deposits.deduct` (same checks and ledger entries) and stores the report on the `DepositEntry`, so the clearance statement links to the evidence.
8. **Capabilities**: `inspections.view` (see reports, the register and photos) and `inspections.record` (start, fill, complete and cancel reports, and edit the register). Both are scoped by property. They are granted by migration to Owner, Manager and Caretaker roles; Leasing Agent gets both; Viewer and Maintenance Manager get view.
9. **Pages**: a "Condition reports" card on the lease page (start or open each kind), the reports and a register link on the unit page, the report page at `/inspections/<report>/` (a form while a draft, read-only and printable once completed), and the register at `/inspections/units/<unit>/items/`. Everything is audited: started, completed, cancelled, photo added or removed, register changes.
10. **Deferred**: the tenant confirming the report in the portal (Phase 7), meter readings (with metered water), a PDF of the report (print from the browser meanwhile), inspections during the tenancy, and AI summaries.

### D-048 — Tenancy and payment record letter — ACCEPTED (2026-09-29, open to change before merge)
D-046 item 3, second part. Built on `feature/condition-reports`, in a new `letters` app. The owner chose: all four facts, each switchable in the organization's settings; a new capability; issue whatever the record shows; numbered, stored and checkable.
1. **A letter states facts, never a judgment.** Titled "Tenancy and payment record", it can be issued whatever the record shows. Before issuing, staff see what it will say and a warning if it shows late or unpaid invoices or money owed.
2. **Always stated**: the tenants on the latest lease, each lease of the tenancy (property, unit, dates) and whether the tenant is current or when the tenancy ended. A tenancy is a lease and the leases it renewed or moved from (`previous_lease`); a letter asked for on any of them covers all of them and is filed on the latest issued one. A draft lease gets no letter.
3. **Chosen by the organization** (`Organization.letter_show_*`, all on by default, changed by `organization.manage` on `/letters/settings/`, audited; letters already issued do not change):
    - **Payment record**: invoices that fell due (issued, not void, above zero, grace period over), split into paid on time, paid late and not yet paid in full. On time means confirmed payments dated on or before the invoice's `overdue_after` (due date plus the lease's grace days) cover it. Reversed payments do not count.
    - **Balance**: the ledger balance over the tenancy that day (owed, in credit, or nothing owed).
    - **Deposit**: the amount held while the tenancy runs, or once it has ended, what was refunded and deducted (net of corrections; transfers inside the tenancy cancel out). Never the reasons for deductions.
    - **Rent**: the rent now, or at the end of the tenancy.
4. **Numbered, stored and frozen.** `TenancyLetter`: LTR-YYYY-NNNNNN per organization, the facts frozen as JSON, an A4 PDF drawn once with ReportLab and served only through a permission-checked view, who issued it and when. Later payments do not change a letter.
5. **Checkable.** Each letter prints a private link `/l/<code>/` (random 16-byte code). Without a login it shows the issuing organization, the number, the date and the same facts in the same words, so a copy can be compared. It is `noindex`, sends no referrer and is limited to 30 views per IP a minute. An unknown code gets 404.
6. **Withdrawn, never deleted.** A letter issued in error is withdrawn with a reason; the check page then says it was withdrawn (not why).
7. **Capability** `tenants.issue_letter` (issue, download and withdraw), scoped by property, granted by migration to Owner and Manager.
8. **Pages**: a "Tenancy letter" card on the lease page, and `/letters/lease/<lease>/` with a preview of what a letter issued today says, the issue button, and the letters of the tenancy. Issuing and withdrawing are audited.
9. **Deferred**: sending the letter to the tenant by SMS or email, a tenant asking for one in the portal (Phase 7), the organization's logo on the letter (with the branding settings page), and letters in Swahili.

### D-049 — Quarterly and yearly billing — ACCEPTED (2026-09-29, open to change before merge)
D-046 item 3, third part. Built on `feature/billing-frequency`, cut from `feature/condition-reports`. The owner chose: periods from the lease's start month, rent entered per month, charges follow the lease.
1. **`Lease.billing_frequency`** offers Monthly (default), Quarterly and Yearly. It is set on the lease form while the lease is a draft and carried over by a renewal or transfer. To change it on an active lease, renew the lease.
2. **Periods count from the lease's start month**: a quarterly lease starting on 15 March bills March–May, June–August and so on, and a yearly one March to February. A partial first or last month is prorated inside its period, as for monthly leases.
3. **Rent and charges stay monthly amounts.** An invoice has one line per month per charge ("Rent April 2026"), so idempotency, proration, rebilling and every monthly figure (rent roll, arrears, letters, reports) work unchanged. The lease page shows the rent per invoice.
4. **Recurring charges follow the lease**: water, garbage and service charge go on the same period invoice. `LeaseCharge.frequency` stays Monthly and is not used.
5. **One invoice per period**, issued `invoice_lead_days` before the period starts (the daily job bills the period that holds this month and, within the lead days, next month), due on the lease's due day in the period's first month, never before the move-in or the issue date. Billing any month of a period, by the job or on "Bill a month", bills the whole period. A lease activated in the middle of a period is billed for the whole of it.
6. **Changes after billing**: when an end date, rent change or charge change makes a period invoice wrong, the whole invoice is voided (if nothing was paid on it) and the period billed again with the original due date. A charge added later is billed on one extra invoice per period. An invoice with a payment is still left for correction by hand, as for monthly leases; this matters more for quarterly and yearly leases, and a credit note for the unused months is the natural follow-up.
7. Invoices show their period as "Jan – Mar 2026" in lists and payment screens.

### D-050 — Annual rental income pack and the landlord's tax residence — ACCEPTED (2026-09-29, open to change before merge)
D-046 items 3 and 4, and the MRI report staged by D-037. Built on `feature/billing-frequency` after D-049. The owner chose: the tax residence lives on the organization.
1. **`Organization.landlord_tax_residence`**: Resident in Kenya (default) or Non-resident. Only people with `organization.manage` change it, from the income page, and each change is audited. One flag per organization; agency mode will need it per property owner (D-040).
2. **The pack** (`/reports/income/`, `reports.view_financial`) covers one calendar year and only the properties the member can see: a summary, a table by month, totals by property and the confirmed payments. Exports need `reports.export`: CSV by month, by property and by payment, and an A4 landscape PDF drawn on request (nothing stored).
3. **Billed** is live invoice lines by the month they bill, so a quarterly invoice counts in its three months (D-049). Deposits are left out.
4. **Received** is confirmed payments dated in the year; a reversed payment is left out whenever it was reversed. Each allocation is split across its invoice's lines by their share, so paying an invoice of rent and water counts partly as rent. Money not yet applied to an invoice is counted as rent and shown on its own line. Deposits received are shown but never taxed.
5. **The tax estimate** is each month's taxable rent (rent received plus unapplied money) times the rate in force that month, rounded per month. Rates are dated in code (`reports/income.py`) **[VERIFY with KRA or a tax adviser]**: resident 10% from 2016 and 7.5% from 1 January 2024; non-resident 10% final tax from 1 July 2026 (Finance Act 2026), with no rate before that, so no estimate is shown for those months. Other charges (water, service charge) are not taxed in the estimate **[VERIFY]**.
6. **Notes on every pack**: the year is not over; only your properties are included; unapplied money counted as rent; months with no rate; a resident's yearly rent outside KES 288,000–15,000,000, the band the monthly regime covers **[VERIFY]** (the low end is only checked once the year is over); expenses are not tracked yet, so figures are gross; "Estimate only. Confirm with your tax adviser before filing."
7. Not in this step: expenses and net income, a withholding statement per owner for agencies, eTIMS or eRITS submission, and filing.

### D-051 — Phase 7 analytics: chart library, live figures, first report set — ACCEPTED (2026-09-29, open to change before merge)
Answers doc 15 §8. The owner said to use the recommended defaults. Built on `feature/phase7-dashboards`, cut from `feature/billing-frequency`.
1. **Chart library: Chart.js 4**, loaded from jsDelivr like Bootstrap, only on pages that draw a chart. No build step. Every chart has a table beside it (for screen readers and as a fallback when JavaScript is off), and the figures behind it export as CSV to people with `reports.export`.
2. **Live figures first, no snapshot table yet.** Monthly trends are grouped queries over invoice lines, allocations and payments, so they come straight from the source. `DailySnapshot` waits until the 10,000-unit performance test (doc 15 §7) shows it is needed, or until arrears over time are wanted (the only trend the source cannot rebuild). This departs from doc 15's recommendation of snapshots from Phase 7.
3. **Trends are "as recorded now"**: a late payment or a reversal changes the past months. The page says so. No "as at date" view yet.
4. **First report set**: a dashboard with five headline numbers (occupancy, rent expected, rent collected, collection rate, arrears), then cash received, a 12-month rent trend, arrears aging and occupancy by property. Next come the who-to-call list with collectability grades, then the monthly owner statement PDF. Income against expenses waits for the expenses app (flow C).
5. **One place for definitions**: `reports/metrics.py`, following doc 11 §26.
   - *Rentable* units are live units that are not Inactive, on live properties.
   - *Occupied* units are rentable units with an occupying lease on the day. The day is today for the current month and the month's last day for a past month.
   - *Expected rent* is live rent lines on issued, part-paid or paid invoices, by the month each line bills. A quarterly invoice counts in its three months (D-049).
   - *Collected for the month* is each confirmed payment's allocation, split across its invoice's live lines by their share (as in D-050), keeping the rent lines of that month. It is rounded per invoice and month.
   - *Collection rate* is collected ÷ expected, with no rate when nothing was billed ("no rent billed yet", not 0%).
   - *Cash received* is confirmed payments dated in the month, including advances and arrears clearing.
   - *Arrears* is what is overdue today, from the existing FIFO aging (billing.selectors), in buckets 1–30, 31–60, 61–90 and 90+ days (counted from due date plus grace since D-054).
   - *Unmatched M-Pesa* is the count and amount waiting in the inbox the member can see.
6. **Scope**: the page needs `dashboard.view_financial` and counts only the properties the member can see. A scoped manager never sees organization totals. Filters are one property and a month, and they are kept in the URL. Counts sit beside rates ("8 of 10 units").
7. **Drill-down**: occupancy opens the units list, cash received opens the payments list for that month and property, and arrears opens the arrears page.
8. Not in this step: caching, `DailySnapshot`, per-role dashboards, and the Swahili labels (strings are wrapped, but there is no translation yet).

### D-052 — Collectability grade and the daily who-to-call list — ACCEPTED (2026-09-29, open to change before merge)
Phase 7 step 2 (D-051 item 4; doc 16 Tier 2 item 7). The rules are rule-based and explainable; there is no machine learning. Built on `feature/phase7-dashboards`.
1. **Tenancy, not lease**: a grade covers a lease and the leases it renewed or moved from (`previous_lease`), so a renewal keeps its history.
2. **Days late per invoice**: this is how long after `overdue_after` (due date plus grace) the invoice was paid in full, using the dates of its confirmed allocations. It is 0 when paid in time, which is the same "on time" the tenancy letter uses (D-048). An invoice still unpaid counts its days late up to today. Opening balances have no invoice and are not graded.
3. **Window**: the tenancy's last 6 invoices whose `overdue_after` has passed, within 12 months. A tenancy with fewer than 2 such invoices gets no grade and shows as "New".
4. **Grade from the average days late**:
   - **A**: every invoice was paid in time.
   - **B**: 5 days or fewer.
   - **C**: 15 days or fewer.
   - **D**: 30 days or fewer.
   - **E**: more than 30 days.

   An invoice unpaid more than 30 days past `overdue_after` caps the grade at D, and more than 60 days caps it at E. The page shows the average and the count ("4 of 6 paid in time"), so a grade can be checked.
5. **Follow-ups** (`billing.FollowUp`, append-only) record an outcome: spoke to them, no answer, promised to pay, disputes the amount, or other. Each has a short note, and a promise has a date and an optional amount. Recording one needs the new capability `arrears.follow_up`, granted to Owner, Manager and Accountant by accounts 0010. Only the latest follow-up counts.
6. **Promise state**: a promise is *pending* until its date. It is *kept* when confirmed payments dated from the day it was made to its date add up to the promised amount (or any payment, when no amount was given). Otherwise it is *broken* once its date passes.
7. **The list** (`/billing/call-list/`, `invoices.view`) covers leases with money overdue today, in the member's property scope. It has three sections:
   - *To call*: broken promises first, then the largest amount overdue.
   - *Promised*: pending promises, by date.
   - *Done today*: leases followed up today.

   It can be filtered by property and grade. Phone numbers show to people with `tenants.view`, as `tel:` links.
8. Not in this step: reminder sequences, the grade in the tenancy letter or visible to tenants, and grade history.

### D-053 — Property owners and the monthly owner statement — ACCEPTED (2026-09-29, open to change before merge)
Phase 7 step 3 (D-051 item 4; doc 12 Tier 2; doc 16 Tier 2 item 8). It builds the D-035 design-in and the statement from doc 14 A9. Built on `feature/phase7-dashboards`. Recommended defaults, taken because the user said "next".
1. **`PropertyOwner`** (properties app): the person or company who owns one or more properties. It has a name, and an optional phone, email and note. It is not a login. `Property.owner` (nullable) and `Property.management_fee_percent` (nullable, 0 to 100) are set on the property form. Owners are added and edited at `/properties/owners/` by people with `properties.manage`. Every change is audited. No deleting.
2. **The statement** (`/reports/owner-statement/`, `reports.view_financial`) covers one owner and one calendar month. It includes only that owner's properties the member can see, archived ones included. Properties with no owner set make up their own statement, addressed to the organization, so a self-managing landlord gets one too. It defaults to last month. The A4 PDF needs `reports.export`. It is drawn on request from the live figures; nothing is stored or numbered yet.
3. **Figures**, per unit and per property:
   - **Billed**: live invoice lines by the month they bill (D-049), without deposits.
   - **Collected**: confirmed payments dated in the month (cash basis, because it is what can be paid over). Each payment is split across its invoices as in D-050: rent, other charges and deposit. Money not yet applied to an invoice counts as rent.
   - **Balance at month end**: the lease's ledger up to the last day of the month.
   - **Occupied**: units at month end, for live properties.
4. **Management fee**: the property's percent times the rent collected (rent plus unapplied money), rounded per property. Water, service charge and other charges carry no fee. No percent set means no fee.
5. **Due to the owner** = rent and other charges collected − the management fee − expenses. Expenses show as "not tracked yet" until the expenses app (flow C). Deposits received are listed, but they are held and not paid over.
6. **Owner login**: invite the owner with the existing Viewer role, limited to their properties. It is read-only and has no tenant names, because Viewer lacks `tenants.view`. Tenant names show on the statement only for members with `tenants.view`. There is no new role or capability.
7. **Not in this step**: remittance records (money paid over to the owner), stored numbered statements, sending the statement by email or SMS, expenses, and a tax residence for each owner (D-050 item 1).

### D-054 — Arrears aged from due date plus grace; global search with suggestions — ACCEPTED (2026-09-29, open to change before merge)
Phase 7 step 4 (doc 11 §17 and §26; doc 12 "Global search"). Built on `feature/phase7-dashboards`. The user said: "do that professionally, add also autocomplete".
1. **Aging basis corrected.** Doc 11 §26 defines arrears as what is outstanding past the due date plus grace. The FIFO aging in `billing.selectors` counted from the due date. Each unpaid amount now carries `late_from`: `Invoice.overdue_after` for an invoice, and the entry date for any other debit (opening balance, deposit deduction). The buckets, `LeaseArrears.days_overdue`, the arrears list order, the call list's overdue amount and days (D-052), the dashboard arrears figure (D-051) and the announcement filter by days overdue all follow it. Money within its grace days now shows as current. `oldest_due` still reports the due date itself.
2. **One search box** in the top bar for every member, plus a results page at `/search/?q=`. It searches:
   - **tenants**: name, contact person, phone or other phone (full number in any format, or 4 or more digits of it), and ID number when the member has `tenants.view_sensitive`;
   - **properties**: name, or the exact code;
   - **units**: code, or the exact payment reference;
   - **leases**: number, exact unit code, or the tenant's name when the member has `tenants.view`;
   - **invoices**: number;
   - **payments**: reference or receipt number;
   - **M-Pesa transactions**: code (exact or leading), account reference, payer phone.
3. **The same rules as the list pages.** A group appears only with that list's view capability (`tenants.view`, `properties.view`, `units.view`, `leases.view`, `invoices.view`, `payments.view`, `mpesa.view_transactions`). It is built from the list's own visibility helper, so a scoped member sees only their properties and nothing crosses organizations. Tenant names are left out of lease results without `tenants.view`. Archived tenants, properties, units and leases are not searched; invoices and payments are, as on their lists.
4. **Ranking**: exact match, then leading match, then anything else, then by name or newest first. Queries shorter than 2 characters return nothing. Queries are trimmed, whitespace collapsed and capped at 60 characters.
5. **Suggestions** (`/search/suggest/?q=`, JSON, `Cache-Control: private, no-store`) return the first 5 per group, with a flag when there are more and a link to that group's own list filtered by the same words. The results page shows 20 per group. The script (`static/js/search.js`, no library):
   - waits 180 ms after typing;
   - cancels a request that a newer one replaces, and caches answers for the page;
   - ignores an answer to a query that is no longer in the box;
   - follows the ARIA combobox pattern (arrow keys, Enter opens, Escape closes, `/` focuses the box), announces the count to screen readers, and highlights the match using text nodes only.
   The box is a plain GET form, so search works without JavaScript.
6. **Indexes**: every query is first limited to one organization, which is indexed. Matching inside a value (`icontains`) scans that organization's rows, which is fine at the sizes in doc 11 §27. Postgres trigram indexes (`pg_trgm`) wait until the performance test shows a need. No migration.
7. **Not in this step**: searching notes, messages, letters or inspections; spelling tolerance; recent searches.

### D-055 — Tenant portal: invitation, account and read-only tenant pages — ACCEPTED (2026-09-29, open to change before merge)
Phase 7 step 5 (doc 11 §6 and §18; doc 12 "Tenant portal"; D-039 moved it earlier). Built on `feature/phase7-dashboards`. Recommended defaults, taken because the user said "next".
1. **Same login as staff.** A tenant signs up or logs in with the phone number and a password, and verifies the phone by SMS code, exactly as staff do. No new way to log in, so no new attack surface. One `User` can be staff in one organization and a tenant in another, or both in the same one.
2. **`TenantAccount`** (new `portal` app) links a `User` to one `Tenant` record. A tenant has at most one live account; revoking keeps the row, and a later invitation makes a new one. One user may hold accounts in several organizations and sees them all together.
3. **Invitation** (`PortalInvitation`, `tenants.invite_portal`, already granted to Owner and Manager):
   - Staff invite from the tenant's page.
   - The SMS carries a link with a random token; only its SHA-256 hash is stored. The link lasts 7 days, and a new invitation replaces any pending one.
   - Accepting needs a logged-in user whose **verified phone equals the invited phone and the tenant's current phone**. That is the claim rule of doc 11 §6.
   - Staff can revoke a pending invitation or a live account; both are audited.
   - No invitation is sent when the tenant turned SMS off (D-044), or when they have no lease past draft (there would be nothing to show).
4. **What a tenant sees** (doc 11 §18), read-only:
   - each of their leases that is not a draft (unit, property, rent, dates, status);
   - the balance and the next amount due with its date;
   - open invoices;
   - a 12-month statement with a running balance;
   - confirmed payments, with their receipt PDFs;
   - the "Pay with M-Pesa" link when the lease has one (D-046).
   Co-tenants on a joint lease each see that lease. Staff names, notes, audit data and other tenants on the property are never shown.
5. **Isolation.** Every portal query starts from the user's own lease IDs: live accounts, of tenants not archived, in organizations not archived. Anything else is a 404. Leakage tests cover:
   - another tenant in the same organization;
   - another organization;
   - a revoked account;
   - an archived tenant;
   - draft leases;
   - receipts of reversed payments;
   - staff pages opened by a tenant.
6. **Routing.** A logged-in user with no staff membership but a live tenant account goes to `/my/` instead of the "create your workspace" page. A user with both sees "My home" in the top bar. Tenant-only users get a small top bar without staff links.
7. **Not in this step**: maintenance requests, notices and shared documents in the portal; in-app messages to tenants; paying inside the portal other than through the existing pay link; Swahili; per-role staff dashboards (still open in TODO).

### D-056 — Per-role home pages — ACCEPTED (2026-09-29, open to change before merge)
Phase 7 step 6, the last open item (doc 11 §17; doc 12 "per-role home pages"; D-051 item 8). Built on `feature/phase7-dashboards`. The user said "do it".
1. **By capability, not by role.** Roles are data (doc 13), so the home page (`/`) has no per-role templates. Each part appears only when the member holds the capability of the page it links to, so a link never ends on "not allowed". The default templates therefore get different home pages, and a renamed or custom role gets the right one without code. The logic is in `reports/home.py`.
2. **"Today" comes first**: counts of work waiting, each linking to the page that deals with it. An item with nothing waiting is hidden. When nothing is waiting, the page says "Nothing is waiting for you."
   - *Payments to confirm*: `payments.confirm`; opens the review queue.
   - *M-Pesa payments to match*: `mpesa.match`; opens the inbox.
   - *Tenants to call*, with broken promises noted, and *promises to pay due today*: `invoices.view` and `arrears.follow_up`; opens the call list (D-052). A Viewer can open the list but is not given calls to make.
   - *Draft leases to check and activate*: `leases.activate`.
   - *Units available to let*: `units.view` and `units.list_vacant`, shown to those who can let them (`leases.draft` or `prospects.manage`).
   - *Leases ending within 60 days* (the existing "expiring" rule), the soonest 5: `leases.view`. The tenant's name shows only with `tenants.view`.
   - *Condition reports to finish* (drafts), the oldest 5: `inspections.record`.
3. **Figures.** With `dashboard.view_financial`, the page shows the existing collections card (D-051). With only `dashboard.view_summary`, which every default template holds, it shows counts and no amounts or names: units occupied out of rentable, vacant (with how many are reserved or under repair), and how many leases have rent overdue. This is doc 10's "occupancy and arrears count only" for a caretaker. The overdue count uses the same aging as the arrears list, without `invoices.view`.
4. **Scope.** Every count is built from its list page's own visibility helper, so it covers only the member's properties and matches what that page lists.
5. **Tests**: a table for the eight default templates, a scoped manager, promises (due today and broken), and a caretaker's page showing no money, names or links they cannot open.
6. **Not in this step**: maintenance jobs (no maintenance app yet), rearranging or hiding cards per person, caching, and notifications for the same items.

### D-057 — Metered water — ACCEPTED (2026-09-29, open to change before merge)
D-046 item 2, moved to right after Phase 7. Built on `feature/metered-water`, cut from `feature/phase7-dashboards`. The user said to go ahead, with one change: **a photo of the meter is optional, never required.**
1. **Meters** (`meters.Meter`) belong to a property: a label (e.g. "A1 water" or the serial), `kind` Postpaid or Prepaid, the units it serves, the rate per m³ and an optional minimum charge. A meter serving one unit is that unit's own meter; one serving several is shared, and splits its bill **equally** or **by weight** (`MeterUnit.weight`). A flat water fee is not a meter: it stays a recurring Water charge on the lease. **Prepaid meters** can be read but are never billed ("not billed" on every page).
2. **Readings** (`MeterReading`): the date, the value in m³ (three decimals), an optional photo, an optional note, who recorded it and when. The first reading of a meter, and a reading marked "meter replaced", is a **baseline**: it starts the count and bills nothing. A reading must be dated after the meter's latest live reading and not in the future.
3. **Flags**, worked out when the reading is recorded:
   - *Lower than last time*: cannot be approved. Reject it, or record it again as "meter replaced".
   - *No water used* while a served unit was let.
   - *Much higher than usual*: more than 3 times the daily average of the last three approved periods, and at least 5 m³ over it.
   - *Long gap*: more than 45 days since the last reading.
   Approving a flagged reading needs a note saying why.
4. **Approval before billing.** Readings wait as Submitted until someone with `meters.approve` approves or rejects them (a reason is required to reject). The approver takes the rate in force then, and it is stored on the reading. A member who holds both capabilities may approve their own reading, since small landlords work alone; the audit log shows who did both.
5. **What each lease pays.** Usage × rate for the meter. A shared meter splits that by weight across **all** the units it serves; the minimum charge applies to each unit's share. Each unit's share is divided between the leases that covered it by days, over the days from the day after the last reading to the reading date. Days with no lease are not billed: the landlord carries a vacant unit's water. Each lease's amount is rounded once (`MeterCharge`).
6. **Billing.** A charge is billed in the month after the reading (a reading on 30 September goes on October's invoice). For a lease that has ended, it is billed in the lease's last month instead. It is a separate line on the invoice showing the readings, e.g. "Water A1: 1,234.000 → 1,250.500 m³ = 16.500 m³ × 150.00 (1 Sep – 30 Sep)". If that period is already invoiced, a small extra invoice holds the water lines. Approval bills it at once when its month is already being billed; otherwise the daily job picks it up with the rent. A line points at its `MeterCharge`, so each charge is billed once (a constraint), and voiding the invoice frees it to be billed again.
7. **Undoing an approval** is allowed only while none of its charges is on a live invoice (void the invoice first). The reading goes back to Submitted and its charges are cancelled.
8. **Capabilities**: `meters.view` (Owner, Manager, Accountant, Caretaker, Viewer), `meters.record` (Owner, Manager, Caretaker), `meters.approve` (Owner, Manager, Accountant) and `meters.manage` to add meters and set rates (Owner, Manager). They are granted to existing roles by a data migration, as with 0010.
9. **Pages** (`/meters/`): the meters of each property, with their last reading; a phone-friendly **reading round** for a property (one box per meter, the last value shown, an optional photo each); the approval queue with flags and bulk approval of unflagged readings; a meter page with its history and charges. The home page gets a "meter readings to approve" item.
10. **Not in this step**: electricity meters, tenant-submitted readings, readings in the portal (the invoice line already shows them), SMS about high usage, tiered (block) tariffs, and a standing charge.
