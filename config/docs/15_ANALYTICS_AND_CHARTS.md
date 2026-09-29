# 15 — Analytics, Charts and Analysis

Builds on doc 11 §26 (metric definitions). Rule: **one metric, one definition, one place** (`reports/metrics.py`). Every chart, PDF, export and AI answer reads from it, so numbers never disagree.

## 1. What to chart (by audience)

| Audience | Charts |
|---|---|
| Owner | Collection rate trend (12 months); income vs expenses vs net operating income by month; occupancy by property; arrears aging (stacked bars 0–30/31–60/61–90/90+); top 10 arrears tenants; vacancy loss; rent by property (share) |
| Manager | Today's collections; overdue list by building; lease expiries in next 90 days (timeline); maintenance open/aging; unallocated payments count |
| Accountant | Payments by method (M-Pesa/cash/bank); cash vs confirmed; deposits held liability; expenses by category; reconciliation gaps |
| Caretaker | Own cash recorded today/this week, units to collect from, simple progress bar (mobile only) |
| Tenant | Own balance and payment history only |
| Platform Admin | Active organizations, units under management, activation time, MRR, churn, SMS volume and cost, failed callbacks. **No tenant-level data.** |

Prefer simple charts: bar, line, stacked bar, donut only for shares. Avoid decoration. Every chart has a table view and CSV export (accessibility and trust).

## 2. Data design so analytics stays fast and correct
1. **Aggregate from the ledger and allocations**, never from ad-hoc sums in templates.
2. **Period rule:** Africa/Nairobi month boundaries; store `period` (YYYY-MM) on invoices and allocations.
3. **Snapshots:** nightly `DailySnapshot(organization, property, date, occupied, rentable, expected, collected, arrears_by_bucket, expenses)`. Trends read snapshots; today's numbers read live. Snapshots are rebuildable from source and are never the source of truth.
4. **Backdated changes:** a late payment or reversal changes the past. Trends show *as recorded now*, with an optional "as at date" view later. Decide and state it in the UI.
5. **Cash vs accrual:** show both, labelled: *Collected for period* (accrual by invoice period) and *Cash received* (by payment date). Doc 11 §26.
6. **Excluded from property analysis:** platform subscription fees, SMS top-ups; deposits shown in their own section (flows A, B, C stay separate, doc 11 §22).
7. **Permissions:** a report obeys capability and property scope (`reports.view_financial`). A manager scoped to one property never sees portfolio totals. Test this.
8. **Indexes and caching:** indexes on (organization, period), (organization, property, period); cache dashboard totals, invalidate on invoice/payment change; heavy reports as background jobs.
9. **Small-sample honesty:** with few units, percentages swing. Show counts beside rates ("8 of 10 units paid").

## 3. Analysis worth building (by phase)
- **MVP dashboards:** occupancy, expected vs collected, collection rate, arrears aging, cash received, unallocated payments.
- **Later insights:** average days late per tenant, chronic late payers, rent vs market (needs `Unit.list_rent`), vacancy duration, turnover rate, expense per unit and per category, maintenance cost per property, arrears recovery rate, seasonality.
- **Forecasts (later, simple first):** expected collections next month from active leases and scheduled rent changes; lease-expiry cash risk. Use plain arithmetic before machine learning.
- **Alerts:** rent collection below a threshold by day 10, arrears bucket growing, expiring leases, unusual cash edits (fraud signal).
- **Benchmarks (much later, opt-in, anonymised):** area averages need enough organizations to avoid identifying anyone.

## 4. Technology choices
| Option | Use for | Note |
|---|---|---|
| **Chart.js** or **ApexCharts** | MVP charts in Django templates | Small, works on mobile, no build step needed. Recommended |
| HTMX partials | Refresh a chart when filters change | Matches the chosen stack |
| Server-side PNG/SVG in PDFs | Reports for owners | Render charts in PDF via WeasyPrint with pre-rendered SVG |
| Metabase / Superset | Internal Platform Admin analytics only | Not exposed to customers at first |
| Pandas | Heavy offline analysis and exports | Not in request path |
| JSON endpoints | `/reports/api/...` returning aggregated numbers | Money as strings (doc 11 §25); same permission checks |

Avoid a heavy SPA framework; the audience is on low-end phones and slow data.

## 5. UX rules
- Dashboard shows five numbers first (occupancy, expected, collected, collection rate, arrears), charts below.
- Every number is clickable to the underlying list (drill-down): "KES 84,000 arrears" opens the tenants who owe it.
- Filters: property, building, period, unit type; the URL keeps the filter so views are shareable.
- Empty states and first-month states explain themselves (no data yet is not "0%").
- Print/PDF friendly reports for landlords who show them to banks, co-owners and family.
- Swahili labels and simple wording ("Amount owed", not "Receivables").

## 6. Privacy and safety in analytics
- Platform analytics use aggregates; no tenant names, phones or ID numbers.
- Exports need `reports.export` and are audited (who, what, when).
- Product analytics (our own usage tracking) is privacy-friendly and free of tenant personal data (doc 14 C11).
- AI assistant questions like "which tenants are late?" go through the same permission-aware metric layer, never raw SQL.

## 7. Testing
- Fixture with known data and hand-calculated expected metrics; test each definition (occupancy, collection rate, aging buckets, NOI).
- Test month boundaries, leap February, mid-month move-ins, reversals, partial payments and overpayments.
- Test that scoped users cannot see out-of-scope totals.
- Performance test with 10,000 units and 50,000 invoices (doc 14 C6).

## 8. Decisions needed
Answered by D-051 (2026-09-29): Chart.js; live queries first, no snapshots yet; "as recorded now"; the dashboard first, then the who-to-call list and the owner statement.

1. Chart library: Chart.js (recommended) vs ApexCharts.
2. Do trends use nightly snapshots from day one (recommended from Phase 7) or live queries first?
3. "As recorded now" vs "as at date" for historic trends (recommend as recorded now).
4. First report set for pilots: rent roll, collection by property, arrears aging, occupancy, income vs expenses (recommended).
