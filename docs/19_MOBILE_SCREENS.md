# 19 — The 10 key screens on a phone (D-096)

Doc 14 C2 asked for the 10 key mobile screens to be sketched before templates were written. The templates came first,
so this records what each screen does on a phone 360 pixels wide, what was checked, and what was fixed. A native app
would start from this list and the design system below.

Checked on 2026-10-01 against the templates and the CSS. Not yet tried on a real phone; that is listed under "Still open".

## The screens

| # | Screen | Page | Main action on a phone | Shown first | Found and fixed |
|---|---|---|---|---|---|
| 1 | Home | `/` (`accounts/home.html`) | The day's tasks | Name, "Get set up" while unfinished, Today's tasks, then summary cards | Nothing to fix. Cards stack; no tables. |
| 2 | Units | `/properties/units/` (`properties/unit_list.html`) | Find a unit, show vacant | Filter, then one card per unit with its status badge | Nothing to fix. |
| 3 | Unit | `properties/unit_detail.html` | New lease, report a problem | Unit code, status, current lease, then leases, repairs, items, prospects | Nothing to fix. Small buttons were below 40 px on touch screens (fixed for all pages, item 4). |
| 4 | Tenant | `tenants/tenant_detail.html` | See leases, message settings | Name, leases, message settings | Nothing to fix apart from the button size. |
| 5 | Record a payment | `payments/record.html` | Record payment (full-width large button) | Amount with the number keyboard and "Full balance" shortcut, method, reference | Already had the number keyboard. The allocation table could push the page sideways: now scrolls in its card. |
| 6 | Invoice | `billing/invoice_detail.html` | PDF, void | Number, tenant and unit, status, lines | The lines table had no scroll wrapper: fixed. |
| 7 | Arrears | `billing/arrears.html` | Who to call | Totals, then the list | Nothing to fix (already wrapped). |
| 8 | Add a lease | `leases/lease_form.html` | Save draft (full-width) | Tenant, dates, rent, deposit | Rent and deposit are number fields; they now also ask for the decimal keypad explicitly (item 5). |
| 9 | M-Pesa inbox of unmatched payments | `mpesa/inbox.html` | Match a payment to a lease | One card per payment with suggested leases; "All caught up" when empty | Nothing to fix. |
| 10 | Tenant portal home | `portal/home.html` | Pay with M-Pesa | You owe, next due, Pay with M-Pesa, then Ask, Report a problem, Statement and receipts | Nothing to fix. |

Other pages fixed in the same pass because they had tables without a scroll wrapper: lease detail (rent history and
charges), move-out, the tenant's account, payment detail, repair detail, the dashboard, owner statements, remittances,
profit and loss, and the subscription page.

## The design system

What the pages already use, written down so new pages and a native app match it. The colours, light and dark mode
and the font are D-111.

- **Base:** Bootstrap 5.3 from the CDN, the Inter font (self-hosted), a light grey page (`#f5f6f8`) with white cards
  (`card shadow-sm`). Content is at most 960 px wide (`.page`); forms for one thing at most 440 px (`.narrow`).
- **Colours and themes:** every colour is a token in `static/css/app.css`, with a light and a dark value. Flat fills
  only, no gradients. Templates use Bootstrap classes (`bg-body`, `text-body-secondary`, `link-body-emphasis`), never
  a colour of their own, so the dark theme works everywhere. One accent (indigo) for primary buttons and links.
  Users pick Light, Dark or System under Account.
- **Lists:** on a phone, one card or list row per item, not a wide table. Tables are kept for money breakdowns, and
  every table is inside `table-responsive` so it scrolls inside its card (checked by a test).
- **Buttons:** one primary action per screen (`btn-primary`, full width on forms); others are outline or link
  buttons. On touch screens small buttons and controls are at least 40 px high. A row holding a heading and buttons,
  or several buttons, has `flex-wrap`, so on a phone the buttons move under the heading instead of squeezing.
- **Top bar:** the staff pages are grouped in four menus (Property, Tenants, Money, More; `accounts/navigation.py`),
  with Help, Security, Install and Log out under Account. It fits one line from 992 px wide; below that it folds
  behind the menu button, and the notifications bell stays beside the button.
- **Long words:** codes, emails and links break inside the page (`overflow-wrap: break-word` on `main`).
- **Status badges:** one meaning per colour.
  - Green (`text-bg-success`): active, paid, done.
  - Blue (`text-bg-primary` or `info`): occupied, draft, for information.
  - Yellow (`text-bg-warning`): needs attention, expiring, partly paid.
  - Red (`text-bg-danger`): overdue, failed, void.
  - Grey (`text-bg-secondary`): ended, inactive, archived.
- **Money:** amounts right-aligned with the currency (`|money`) and tabular figures (`.num`). Money fields open the
  number keyboard (`inputmode="decimal"`); phone fields the phone keyboard (`inputmode="tel"`).
- **Words:** plain words over accounting terms ("Amount owed", not "Receivable"), as doc 14 C3 asks.
- **Alerts:** Bootstrap alerts at the top of the page for messages, the subscription state and the notice about new
  Terms (D-095).

## Still open

- Figma drawings, if a designer joins.
- Checking on real low-end Android phones in the field, together with the meter round (TODO line on field trials).
- Checking the Swahili wording on each screen.
