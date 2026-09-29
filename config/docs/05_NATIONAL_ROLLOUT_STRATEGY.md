# 05 — National Rollout Strategy

Goal: make landlordpms the default tool for every rental owner and manager in Kenya. This is done by winning small first and building trust, not by launching everywhere at once.

## 1. Product principles for reaching everyone
| Principle | Why it matters nationally |
|-----------|--------------------------|
| **Works on cheap Android phones and weak data** | Most users are on mobile, and data is expensive outside cities. Light pages, PWA, offline shell. |
| **Works for someone who has never used software** | Onboarding in a few taps; CSV/WhatsApp import; guided setup. |
| **Swahili + English** (Sheng-friendly wording, simple language) | Reaches beyond English-comfortable users. |
| **Free tier that is actually useful** | A landlord with a few rooms adopts easily, then upgrades as they grow. |
| **SMS/USSD fallback** | Tenants and caretakers on feature phones still receive receipts and can check balances. |
| **Caretaker-friendly** | Caretakers are the real daily users in many blocks. |
| **M-Pesa native** | Rent already moves on M-Pesa; we remove the reconciling work. |

## 2. Go-to-market phases

### Phase A — Prove it (months 0–3): one neighbourhood
- Pick one area (e.g. one estate or town) with many small/medium blocks.
- Onboard **5–10 pilot landlords personally**, in person, free.
- Sit with them through a full rent cycle. Fix everything that confuses them.
- Collect testimonials and hard numbers: hours saved, collection rate change.

### Phase B — Repeat it (months 3–9): one city
- Recruit **caretakers and small agents** as onboarding partners and pay them commission per landlord activated.
- Partner with local landlord associations, estate residents' associations, and property agents.
- Referral program: landlord invites landlord, both get a free month.
- Content: short videos in Swahili/English on TikTok, YouTube, WhatsApp status; "how to stop chasing rent" tips.

### Phase C — Expand (months 9–24): major towns
- Nairobi, then Mombasa, Kisumu, Nakuru, Eldoret, Thika, Kiambu, Machakos, Kajiado (fast-growing rental markets).
- Regional field agents ("landlordpms ambassadors") in each town.
- Partnerships: banks/SACCOs (landlords often have loans), Safaricom business channels, hardware/water/utility vendors, estate agents' bodies **[VERIFY relevant bodies]**.
- Localise: county-specific rent/tax help, local-language support pages.

### Phase D — Every county (24 months +)
- Self-serve growth through referrals and word of mouth.
- Property-management-company and real-estate-developer plans (large accounts).
- API and integrations (accounting, banks).
- Consider neighbouring markets (Uganda, Tanzania, Rwanda) only after Kenya is solid.

## 3. Pricing (validate first)
Preliminary ideas from the original notes, to be tested with pilots:

| Plan | Units | Idea |
|------|-------|------|
| Free | up to 5 | Removes the adoption barrier |
| Starter | ~25 | ~KSh 799/mo |
| Business | ~100 | ~KSh 1,999/mo |
| Professional | ~300 | ~KSh 4,999/mo |
| Enterprise | custom | Agencies and developers |

Other revenue: SMS/WhatsApp bundles, per-transaction fees on M-Pesa collection **[VERIFY legality/fees]**, premium reports, tenant screening/reference services, partnerships.
Pay via M-Pesa (STK Push) with monthly and annual (discounted) options.

## 4. Trust and reliability (the real moat)
Landlords entrust us with money records and tenants' personal data. To be national we must be:
- **Accurate:** no lost or duplicate payments. Ledger, idempotency, reconciliation reports.
- **Available:** backups, monitoring, status page, fast recovery.
- **Private:** compliant with the Data Protection Act; clear consent.
- **Supported:** WhatsApp/phone support in Swahili and English, response-time targets.
- **Transparent:** clear pricing, easy export of your own data at any time (no lock-in fear).

## 5. Channels for tenants (network effect)
Tenants are a distribution channel:
- Every receipt/reminder carries a light "Powered by landlordpms" line.
- Tenants who manage rent for other properties or who are landlords themselves are prompts to sign up.
- Optional tenant portal: statements, payment history, maintenance requests. Payment history could later serve as a **rental reference** (with consent).

## 6. Operations to prepare for scale
| Area | Preparation |
|------|-------------|
| Support | Help center, canned answers, WhatsApp Business, ticketing (support app) |
| Onboarding | Import tools, setup checklist, in-app tips, video tutorials |
| Sales | Agent/partner programme, simple demo script, pilot case studies |
| Infrastructure | Managed Postgres, Redis, object storage, CDN, autoscaling, queue workers |
| Data | Backups tested quarterly, retention policy, export tools |
| Finance | Company registration, KRA compliance, invoicing subscribers, refunds policy |
| Legal | ToS, Privacy Policy, ODPC registration, contracts with SMS/payment providers |

## 7. Metrics to track
- Landlords onboarded, units under management, monthly active landlords
- Rent processed (KSh), % reconciled automatically
- Activation: time to first invoice
- Retention/churn, free → paid conversion
- Support tickets per 100 landlords
- Cost per acquired landlord vs lifetime value

## 8. Risks and mitigations
| Risk | Mitigation |
|------|-----------|
| Landlords stay on notebooks/Excel | Onboard them personally, import from spreadsheet, prove hours saved |
| M-Pesa integration complexity/approvals | Start with manual/record mode; begin Daraja go-live early |
| Handling customer funds (regulation) | Prefer landlord's own shortcode; get legal advice before aggregating |
| Data breach/reputation | Security controls (doc 06), least privilege, audit log |
| Competition (existing PM software, banks) | Focus on simplicity, M-Pesa reconciliation, Swahili, price, support |
| Poor connectivity | PWA, light pages, SMS fallback |
| Founder overload | Ship the MVP only; hire/partner for sales and support early |

## 9. Next actions (this month)
1. Choose the pilot area and list 15 landlords/caretakers to approach.
2. Do 5–10 interviews (see doc 02 log).
3. Decide on collection model (doc 01 section C).
4. Register the business and start ODPC/Daraja processes.
5. Build Phase 1 and 2 of the roadmap while interviews continue.
