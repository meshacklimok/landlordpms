"""Real pictures of the product for the public pages, taken from the "Try the demo" landlord (D-220).

`manage.py capture_screens` opens each page below in Chrome as a sample person of the demo, in light and dark, and
saves WebP files to static/img/screens/ with a small manifest. The pages then show the real screen when its files
are there and fall back to the drawn sample (growth/_landing_mock.html) when they are not, so a fresh checkout or a
missing capture never breaks the home page. The demo's sample data is the only data ever pictured.
"""

import functools
import json
import os
from dataclasses import dataclass

from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.utils.translation import gettext_lazy as _

DIR = "img/screens"
MANIFEST = f"{DIR}/screens.json"
THEMES = ("light", "dark")


@dataclass(frozen=True)
class Shot:
    key: str
    path: str  # the demo page to open; {property} and {lease} are filled in by capture_screens
    caption: str  # a few words under the picture in a gallery
    alt: str
    person: str = "owner"  # who to look around as (accounts.demo.STAFF keys, or "tenant")
    phone: bool = False  # a phone-sized screen instead of a computer one
    crop: str = ""  # CSS selector: the picture starts at this part of the page (the whole window when empty)
    hide: str = ""  # CSS selectors left out of this picture only: things that are there only because it is a demo


SHOTS = [
    # The home page: the hero pair, then one per tab (keyed by the tab's anchor in content.LANDING_BLOCKS).
    Shot("dashboard", "/", _("The landlord's home page"),
         _("The NyumbaPMS home page of a sample landlord: rent collected, still to collect, overdue and occupancy for "
           "the month. Sample data.")),
    Shot("tenant", "/my/", _("The tenant portal"),
         _("The tenant portal on a phone: what the tenant owes, when rent is next due, and how to pay by M-Pesa "
           "Paybill or bank. Sample data."), person="tenant", phone=True),
    Shot("collect", "/mpesa/", _("M-Pesa overview"),
         _("The M-Pesa overview: money received this month, number of payments, the typical payment and payments "
           "waiting to be matched. Sample data."), crop="#overview"),
    Shot("follow-up", "/reports/arrears/", _("Arrears report"),
         _("The arrears report: what is overdue, how late, and by property. Sample data."), crop="main"),
    Shot("manage", "/maintenance/", _("Repairs"),
         _("The repairs list: each repair with its unit, who is on it and its status. Sample data."), crop="main"),
    Shot("see", "/reports/", _("Reports dashboard"),
         _("The reports dashboard: rent collected, collection rate, occupancy, arrears, cash received, expenses and "
           "net operating income, each with a 12-month trend. Sample data."), crop="#overview"),
    # The solution pages and How it works.
    Shot("inbox", "/mpesa/inbox/", _("Payments waiting for one tap"),
         _("The M-Pesa inbox: payments with a wrong or missing account number, each with the unit it most likely "
           "belongs to and a button to match it. Sample data."), crop="main"),
    Shot("transactions", "/mpesa/transactions/", _("Every M-Pesa payment, matched"),
         _("The list of M-Pesa payments: payer, account number, the unit each was placed on and its status. "
           "Sample data."), crop="main"),
    Shot("payments", "/payments/", _("Payments and how tenants pay"),
         _("The payments page: money collected, payments awaiting review, and charts of money received each month "
           "and by method. Sample data."), crop="main"),
    Shot("messages", "/messages/", _("SMS sent for you"),
         _("The message log: each SMS reminder and receipt sent to tenants, with its delivery status. Sample data."),
         crop="main"),
    Shot("invoices", "/billing/invoices/", _("Invoices"),
         _("The invoices list: totals billed, paid, still to pay and overdue, then each invoice with its status. "
           "Sample data."), crop="main"),
    Shot("call-list", "/billing/call-list/", _("Who to call today"),
         _("Who to call: tenants with rent overdue, largest first, with buttons to call or WhatsApp them. "
           "Sample data."), crop="main"),
    Shot("behaviour", "/reports/behaviour/", _("How tenants pay"),
         _("How tenants pay: share paid on time, average days from the due date, and a chart of when invoices are "
           "paid. Sample data."), crop="main"),
    Shot("meters", "/meters/", _("Meters by property"),
         _("The meters page: meters read this month, readings waiting for approval, and each property's meters with "
           "their last reading. Sample data."), crop="main"),
    Shot("water-session", "/meters/water/{property}/", _("A month of water readings"),
         _("Water readings for one property this month: units read, approved and flagged, and ways to record "
           "readings by phone, QR code, Excel or photo. Sample data."), crop="main"),
    Shot("usage", "/meters/usage/", _("Water and electricity used"),
         _("Water and electricity used: billed and charged to tenants, what the landlord pays, and each room's use "
           "and amount. Sample data."), crop="main"),
    Shot("owner-statement", "/reports/owner-statement/", _("Owner statement"),
         _("An owner statement: rent and utilities collected, expenses, the amount due to the owner and each unit's "
           "figures. Sample data."), crop="main"),
    Shot("performance", "/reports/performance/", _("Property performance"),
         _("Property performance: rent earned of potential, net operating income and yield, and a chart from "
           "potential rent to what was collected. Sample data."), crop="main"),
    Shot("properties", "/properties/", _("Your properties"),
         _("The properties list: each property with its area and number of units. Sample data."), crop="main"),
    Shot("meter-round", "/meters/round/{property}/", _("Meter readings on a phone"),
         _("A caretaker reading meters on a phone, room by room; readings are kept on the phone when there is no "
           "signal. Sample data."), person="caretaker", phone=True),
    Shot("mpesa-phone", "/mpesa/", _("M-Pesa rent arriving"),
         _("The M-Pesa overview on a landlord's phone: money received this month with its trend, the number of "
           "payments, and payments waiting to be matched. Sample data."), phone=True,
         hide='[aria-label="Needs attention"]'),  # the demo's Paybill is not connected to Safaricom, so it warns
    Shot("owner-phone", "/", _("Your figures on your phone"),
         _("The landlord's home page on a phone: rent collected, still to collect and overdue. Sample data."),
         phone=True),
    Shot("receipts", "/my/leases/{lease}/payments/", _("Receipts on the tenant's phone"),
         _("The tenant portal on a phone: each payment with its numbered receipt. Sample data."), person="tenant",
         phone=True),
]
BY_KEY = {s.key: s for s in SHOTS}
# The home page hero: the landlord's home on a computer, and M-Pesa rent arriving on their phone.
HERO = ("dashboard", "mpesa-phone")
# Where "Open this screen in the demo" may send a visitor: the owner's pages above, nothing else.
DEMO_PATHS = frozenset(s.path for s in SHOTS if s.person == "owner" and "{" not in s.path)

# Solution pages (growth/solutions.py): (computer screen beside the heading, phone screen in front of it, gallery).
FOR_SOLUTION = {
    "mpesa-rent-collection": ("collect", "receipts", ["inbox", "transactions", "messages"]),
    "software-for-agents-and-property-managers": ("owner-statement", "owner-phone", ["performance", "properties", "see"]),
    "estate-service-charge-and-water-billing": ("invoices", "tenant", ["water-session", "usage", "messages"]),
    "water-and-electricity-billing": ("meters", "meter-round", ["water-session", "usage", "invoices"]),
    "rent-arrears-and-late-fees": ("call-list", "owner-phone", ["follow-up", "behaviour", "messages"]),
    "manage-rental-property-in-kenya-from-abroad": ("performance", "owner-phone", ["see", "manage", "payments"]),
    "water-vendor-billing-software": ("usage", "meter-round", ["meters", "invoices", "inbox"]),
    "garbage-collection-billing-software": ("payments", "receipts", ["invoices", "inbox", "messages"]),
}
# How it works (pages.HOW_STEPS), in order: one screen per step.
FOR_HOW_STEPS = ["properties", "invoices", "transactions", "inbox", "call-list", "see"]


def filename(key: str, theme: str, scale: int) -> str:
    return f"{DIR}/{key}-{theme}{'-2x' if scale == 2 else ''}.webp"


@dataclass(frozen=True)
class Picture:
    """One captured screen as the templates draw it: a 1x `src` and a `srcset` of 1x and 2x for each theme."""
    key: str
    alt: str  # lazy: cached across requests, translated when drawn
    width: int
    height: int
    phone: bool
    path: str  # the demo page it was taken from
    caption: str
    light_src: str
    light_srcset: str
    dark_src: str
    dark_srcset: str


def available() -> dict[str, Picture]:
    """The captured screens whose files are all there, by key. Empty before the first capture."""
    path = finders.find(MANIFEST)
    if not path:
        return {}
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        return {}
    return _load(path, stamp)


@functools.lru_cache(maxsize=2)
def _load(path: str, stamp: int) -> dict[str, Picture]:
    """Read once per manifest version: a new capture changes the file, and with it the cache key and the ?v= below."""
    try:
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        return {}
    version = f"?v={manifest.get('version', '')}"
    out = {}
    for key, size in manifest.get("shots", {}).items():
        shot = BY_KEY.get(key)
        names = {(t, s): filename(key, t, s) for t in THEMES for s in (1, 2)}
        if shot is None or not all(finders.find(n) for n in names.values()):
            continue
        urls = {k: static(n) + version for k, n in names.items()}
        w = size["width"]
        out[key] = Picture(key=key, alt=shot.alt, width=w, height=size["height"], phone=shot.phone, path=shot.path,
                           caption=shot.caption, light_src=urls["light", 1], light_srcset=f"{urls['light', 1]} {w}w, {urls['light', 2]} {w * 2}w",
                           dark_src=urls["dark", 1], dark_srcset=f"{urls['dark', 1]} {w}w, {urls['dark', 2]} {w * 2}w")
    return out


def for_solution(slug: str) -> dict:
    """The captured screens for a solution page: `hero`, `phone` (either may be None) and `gallery` (those there)."""
    pics = available()
    hero, phone, gallery = FOR_SOLUTION.get(slug, (None, None, []))
    return {"hero": pics.get(hero), "phone": pics.get(phone) if pics.get(hero) else None,
            "gallery": [pics[k] for k in gallery if k in pics]}


def pick(keys) -> list:
    """The Picture for each key, None where it has not been captured: lists that pair pictures with other rows."""
    pics = available()
    return [pics.get(k) for k in keys]
