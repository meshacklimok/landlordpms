# NyumbaPMS — Documentation Index

NyumbaPMS is a property-management platform for rental owners in Kenya, designed to scale nationally.

| # | Document | What it answers |
|---|----------|-----------------|
| 0 | [PROJECT_CONTEXT.MD](PROJECT_CONTEXT.MD) | Original master context and status checklist |
| 1 | [01_PRE_BUILD_CHECKLIST.md](01_PRE_BUILD_CHECKLIST.md) | What must be decided/prepared BEFORE writing models |
| 2 | [02_PRODUCT_AND_USERS.md](02_PRODUCT_AND_USERS.md) | Who we serve, their pains, MVP scope |
| 3 | [03_ARCHITECTURE.md](03_ARCHITECTURE.md) | Multi-tenancy, data model, ledger, app boundaries |
| 4 | [04_ROADMAP.md](04_ROADMAP.md) | Phased delivery plan with exit criteria |
| 5 | [05_NATIONAL_ROLLOUT_STRATEGY.md](05_NATIONAL_ROLLOUT_STRATEGY.md) | How this becomes a national product |
| 6 | [06_SECURITY_AND_COMPLIANCE.md](06_SECURITY_AND_COMPLIANCE.md) | Security, Kenyan law, M-Pesa safety |
| 7 | [07_TOOLS_AND_SERVICES.md](07_TOOLS_AND_SERVICES.md) | Dev tools, libraries, third-party services |
| 8 | [08_DECISIONS_LOG.md](08_DECISIONS_LOG.md) | Every architectural decision, with reasons |
| 9 | [09_WORKING_AGREEMENT.md](09_WORKING_AGREEMENT.md) | How we work: git, testing, docs, definition of done |
| 10 | [10_USERS_ROLES_PERMISSIONS.md](10_USERS_ROLES_PERMISSIONS.md) | Roles, seat limits, who can see and do what |
| 11 | [11_DOMAIN_MODEL_BRAINSTORM.md](11_DOMAIN_MODEL_BRAINSTORM.md) | Entities, relationships, lifecycles, answers to open design questions |
| 12 | [12_IMPLEMENTATION_SCOPE.md](12_IMPLEMENTATION_SCOPE.md) | What we build now, design-in, or defer |
| 13 | [13_ROLES_AND_CAPABILITIES.md](13_ROLES_AND_CAPABILITIES.md) | Fully editable roles, capability catalog, per-person overrides |
| 14 | [14_REMAINING_BRAINSTORM.md](14_REMAINING_BRAINSTORM.md) | Deposits, opening balances, tax, fraud, offline, ops, and recommended decisions |
| 15 | [15_ANALYTICS_AND_CHARTS.md](15_ANALYTICS_AND_CHARTS.md) | Charts, analysis, snapshots, chart libraries, report rules |
| 16 | [16_COMPETITOR_BENCHMARK.md](16_COMPETITOR_BENCHMARK.md) | Competitor benchmark, lessons, where we can win (Part 3: 21 websites and apps, 2026-09-29) |
| 17 | [17_MPESA_GO_LIVE.md](17_MPESA_GO_LIVE.md) | M-Pesa go-live checklist: settings, Safaricom approval, first live day |
| 18 | [18_OPERATIONS.md](18_OPERATIONS.md) | Running the server: cron, environment, deploying, backups, alerts |
| 19 | [19_MOBILE_SCREENS.md](19_MOBILE_SCREENS.md) | The 10 key screens on a phone (D-096) |
| 20 | [20_DATABASE.md](20_DATABASE.md) | The database item by item: connections, integrity, monitoring, recovery, scale (D-105) |
| 21 | [21_HETZNER_SETUP.md](21_HETZNER_SETUP.md) | Putting the app live on a Hetzner server, step by step: setup, updates, the monthly check (D-122). Most steps also apply on DigitalOcean (doc 22) |
| 22 | [22_DIGITALOCEAN_SETUP.md](22_DIGITALOCEAN_SETUP.md) | The pilot server on DigitalOcean as actually set up: region, sizes, card problems, upload, resize to the first client's size (D-134) |
| – | [erd.md](erd.md) | Entity relationship diagrams (Mermaid), generated from the models by `manage.py erd` (D-103) |
| – | [../../TODO.md](../../TODO.md) | Working checklist |

## Rules
- The product name is **NyumbaPMS** (D-121); the app lives at https://app.nyumbapms.com. The repository, database and deploy paths keep the old name `landlordpms`.
- Any decision that changes architecture is recorded in `08_DECISIONS_LOG.md` the same day.
- Update `04_ROADMAP.md` status boxes as work completes.
- Statements about Kenyan law, tax rates, and Safaricom/Daraja behaviour are marked **[VERIFY]** where they must be confirmed with a lawyer, KRA, or Safaricom before launch.
