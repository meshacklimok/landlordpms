# 06 — Security and Compliance

## Application security checklist
- [ ] `DEBUG=False`, strong random `SECRET_KEY`, restricted `ALLOWED_HOSTS` in production
- [ ] HTTPS only: `SECURE_SSL_REDIRECT`, HSTS, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`
- [ ] Argon2/PBKDF2 password hashing (Django default is fine), password validators on
- [ ] Login rate limiting / lockout (django-axes or similar), optional 2FA for Owners
- [ ] Every view requires login + org membership + role check
- [ ] Every queryset org-scoped; automated cross-tenant tests
- [ ] Object-level access: never trust IDs in URLs; look up within the active org
- [ ] Server-side validation of all money fields
- [ ] Use Django ORM (no raw SQL with string formatting); template auto-escaping on
- [ ] File uploads: check type, extension, size; store outside web root or in private object storage; serve via signed URLs
- [ ] Secrets only in environment variables; never in Git (`.env` is ignored)
- [ ] Encrypt M-Pesa consumer secrets/passkeys at rest (e.g. `django-cryptography` or KMS)
- [ ] Dependency scanning (`pip-audit`, Dependabot)
- [ ] Security headers (CSP, X-Content-Type-Options, Referrer-Policy)
- [ ] Logs must not contain passwords, tokens, or full ID numbers

## M-Pesa callback safety
1. Unguessable per-organization token in the callback URL path.
2. Optionally restrict to Safaricom's published IP ranges **[VERIFY current list]**.
3. Store the raw payload first, then process.
4. `unique` on `TransID`; processing is idempotent.
5. Validate amount/shortcode against what you expect. Never trust the payload blindly.
6. Never expose Daraja credentials to the browser.
7. Alert on unmatched payments and repeated failures.
8. Reconcile daily against a Daraja statement/report.

## Data protection (Kenya Data Protection Act, 2019) **[VERIFY with counsel]**
We process personal data of tenants (names, phone numbers, national ID, payment history). Requirements to plan for:
- **Register with the ODPC** as a data controller/processor.
- **Lawful basis and consent:** a landlord (controller or joint controller) needs a basis to hold tenant data; our ToS must make roles clear (likely we are a processor for the landlord).
- **Data minimisation:** collect only what is needed. Do not require ID number unless the landlord needs it.
- **Purpose limitation** and **retention policy:** delete or anonymise data after leases end plus a defined period, subject to tax/legal record keeping.
- **Subject rights:** access, correction, deletion, portability. Provide an export and delete process.
- **Breach notification:** have an incident plan (the Act requires notifying the ODPC within a short window; confirm exact timing).
- **Cross-border transfers:** where servers are hosted matters. Check the Act's rules on transfers **[VERIFY]**.
- **Privacy notice** shown at sign-up and to tenants when their data is added.
- **Do not send** marketing SMS without consent. Include opt-out.

## Tax and rental law touchpoints **[VERIFY all]**
- **Monthly Rental Income (MRI) tax:** residential landlords earning within the KRA thresholds pay a flat percentage of gross rent, filed monthly through iTax. Reports should give the monthly gross rent figure. Confirm current rate and thresholds with KRA.
- **Landlord and tenant law:** Rent Restriction Act and Landlord and Tenant (Shops, Hotels and Catering Establishments) Act cover disputes and notices. The Rent Restriction Tribunal handles some residential disputes. Do not present the software as legal advice; add disclaimers.
- **Deposits:** track clearly, since refund disputes are common.
- **Electronic records and signatures:** the Kenya Information and Communications Act recognises electronic records; confirm before offering e-signed leases.
- **Financial regulation:** holding or moving tenants' money may bring the platform under CBK/payment-service-provider rules. Avoid until advised.

## Backups and recovery
- Daily automated Postgres backups, retained 30 days, plus weekly to a second location.
- Test a restore every quarter. A backup you haven't restored is not a backup.
- Target: RPO ≤ 24h at launch (improve later with point-in-time recovery), RTO ≤ 4h.

## Incident response (minimum)
1. Detect (monitoring/alerts). 2. Contain (rotate keys, disable accounts). 3. Assess what data was affected. 4. Notify ODPC/users as required. 5. Fix and write a post-mortem.

## Access to production
- Least privilege. No shared accounts. MFA on GitHub, hosting, email, domain, Daraja, SMS accounts.
- Record who has access to what.
