# 07 — Tools and Services

## Python libraries (add as needed, pin versions)
| Purpose | Recommendation |
|---------|----------------|
| Settings/env | `python-dotenv` (or `django-environ`), `dj-database-url` |
| Database | `psycopg[binary]` (PostgreSQL) |
| Background jobs | `celery` + `redis` |
| REST API (later) | `djangorestframework`, `drf-spectacular` (OpenAPI docs) |
| Forms/UI | `django-crispy-forms` + `crispy-bootstrap5`, `django-htmx` |
| Filtering/tables | `django-filter`, `django-tables2` |
| PDF | `reportlab` (or `weasyprint`) |
| Phone numbers | `phonenumbers` / `django-phonenumber-field` (Kenyan formats: +254, 07xx, 01xx) |
| Money | Python `Decimal`, `django-money` (optional) |
| Auth hardening | `django-axes`, `django-otp` (2FA) |
| Encryption | `cryptography` / `django-cryptography` |
| Storage | `django-storages` (S3-compatible) |
| Static files | `whitenoise` |
| Production server | `gunicorn` |
| Monitoring | `sentry-sdk` |
| Testing | `pytest`, `pytest-django`, `factory_boy`, `freezegun`, `coverage` |
| Quality | `ruff`, `pre-commit`, `pip-audit`, `django-debug-toolbar` (dev) |
| Import/export | `django-import-export` (CSV/Excel for units and tenants) |
| Auditing | `django-simple-history` (row history), plus our own `AuditEvent` |
| i18n | Django gettext (English + Swahili) |

## External services
| Need | Options | Notes |
|------|---------|-------|
| M-Pesa | Safaricom **Daraja** (C2B, STK Push) | Sandbox first. Go-live needs approval **[VERIFY]** |
| SMS / USSD | **Africa's Talking** | Kenyan coverage, USSD later |
| WhatsApp | Meta WhatsApp Cloud API, or a local BSP | Template approval required |
| Email | Amazon SES, Brevo, Mailgun | For receipts/reports |
| Hosting | Render, Railway, Fly.io, DigitalOcean, AWS (Cape Town/Ireland regions) | Latency and data-residency tradeoffs **[VERIFY]** |
| Database | Managed PostgreSQL (Neon, Supabase, RDS, DO Managed) | |
| Cache/queue | Managed Redis (Upstash etc.) | |
| File storage | S3-compatible (Backblaze B2, Cloudflare R2, S3) | Private buckets |
| CDN/DNS | Cloudflare | Also gives DDoS protection |
| Errors | Sentry | |
| Uptime | UptimeRobot / Better Stack | |
| Analytics | PostHog/Plausible (privacy-friendly) | |
| Support | WhatsApp Business + a helpdesk (Crisp, Freshdesk) | |
| Docs/help center | Static site (MkDocs) or Notion | |

## Developer tooling
- **VS Code** + Python, Django, Ruff extensions
- **Git + GitHub** (private repo, PR workflow, Actions for CI)
- **Docker Desktop** (local Postgres + Redis, matches production)
- **Postman / Bruno** for testing Daraja and API calls
- **ngrok / Cloudflare Tunnel** to receive M-Pesa sandbox callbacks locally
- **DBeaver / pgAdmin** for inspecting the database
- **Figma** (free) for mobile-first wireframes before building screens
- **Password manager** (Bitwarden)

## Other apps/products that help the business
- **Google Sheets/Forms** to run landlord interviews and track pilots
- **WhatsApp Business** for support and pilot communication
- **Canva** for marketing material
- **A simple CRM** (HubSpot free, or Notion/Sheets) to track leads and agents
- **Wave/Zoho Books or QuickBooks** for our own company accounting
- **Cal.com/Calendly** for demo bookings

## Later product surfaces
1. **Installable PWA** (first): works for landlords and caretakers.
2. **Tenant portal/lite page** (link in SMS/WhatsApp).
3. **USSD menu** for feature phones (balance, receipt).
4. **Native mobile app** (React Native/Flutter), only after the API is stable.
5. **Public API + webhooks** for agencies and accounting tools.
