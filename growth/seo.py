"""Search and AI visibility for the public site (D-158).

One place says which pages may be found, what each is called, and what a search engine or an AI assistant should
read about it. The same registry feeds the page head (title, description, canonical, Open Graph, Twitter, JSON-LD),
sitemap.xml, robots.txt, llms.txt and the noindex header on everything else. Anything not listed here is private
by default: a new signed-in page can never leak into search results because someone forgot a tag.

Copy is written for how Kenyans search (M-Pesa, Paybill, Till, caretaker, plot of flats, agents, diaspora). Nothing
is claimed that is not built, and no prices are written here: the plans supply them.
"""

import json
from dataclasses import dataclass, field

from django.conf import settings
from django.templatetags.static import static
from django.urls import reverse
from django.utils.translation import gettext as _

from . import content, pages, solutions

SITE_NAME = "NyumbaPMS"
LOCALE = "en_KE"        # Open Graph
LANG = "en-KE"          # html lang and hreflang: English as written and read in Kenya
OG_IMAGE = "img/og-default.png"

# Crawlers that read the site for search or for AI answers. We want to be found and quoted, so all are welcome on
# the public pages. They are named so a future decision to block one is a one-line change (D-158).
SEARCH_BOTS = ["Googlebot", "Bingbot", "DuckDuckBot", "Applebot", "YandexBot", "Baiduspider"]
AI_SEARCH_BOTS = [
    "OAI-SearchBot", "ChatGPT-User", "GPTBot",                   # OpenAI: ChatGPT search, browsing, training
    "Claude-SearchBot", "Claude-User", "ClaudeBot", "anthropic-ai",   # Anthropic: Claude
    "Google-Extended", "GoogleOther", "Google-CloudVertexBot",   # Google: Gemini and AI Overviews use Googlebot too
    "PerplexityBot", "Perplexity-User",                          # Perplexity
    "Applebot-Extended",                                         # Apple Intelligence
    "Meta-ExternalAgent", "Meta-ExternalFetcher", "FacebookBot",  # Meta AI
    "Amazonbot", "DuckAssistBot", "MistralAI-User", "cohere-ai", "CCBot", "YouBot", "PhindBot",
]

# Never offered to crawlers: the signed-in app, tenant and owner portals, webhooks, one-time links and the API.
PRIVATE_PREFIXES = [
    "/ok-ml-staff/", "/my/", "/owner/", "/hooks/", "/api/", "/p/", "/r/", "/sign/", "/s/", "/l/", "/v/", "/e/",
    "/esign/", "/console/", "/conversations/", "/csp-report/", "/healthz", "/search/", "/ask/", "/properties/",
    "/tenants/", "/leases/", "/imports/", "/billing/", "/payments/", "/messages/", "/mpesa/", "/bank/",
    "/inspections/", "/meters/", "/expenses/", "/maintenance/", "/documents/", "/prospects/", "/letters/",
    "/reports/", "/subscription/", "/staff/", "/roles/", "/organization/", "/account/", "/welcome/", "/switch/",
    "/verify-phone/", "/verify-email/", "/invite/", "/help/", "/refer/", "/terms/accept/", "/offline/",
]


@dataclass
class Page:
    title: str
    description: str
    path: str
    kind: str = "website"            # Open Graph type
    schema: str = "WebPage"          # schema.org type of the page itself
    priority: str = "0.5"
    changefreq: str = "monthly"
    breadcrumb: list = field(default_factory=list)   # [(name, path)] between Home and this page
    graph: list = field(default_factory=list)        # extra JSON-LD nodes (FAQPage, HowTo, Article, offers)
    summary: str = ""                # one line for llms.txt when it differs from the description


def absolute(path: str) -> str:
    return f"{settings.SITE_URL}{path}"


def dump(data) -> str:
    """JSON for a <script type="application/ld+json"> block. Browsers never run it, so the script policy does not
    apply; `<`, `>` and `&` are escaped so no text can close the tag."""
    return (json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


def _faq_node(pairs):
    return {"@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": str(q), "acceptedAnswer": {"@type": "Answer", "text": str(a)}}
        for q, a in pairs]}


def _plans():
    from subscriptions import services as subscription_services
    return list(subscription_services.self_serve_plans().filter(monthly_price__gt=0))


def _software_node():
    node = {"@type": "SoftwareApplication", "@id": absolute("/#software"), "name": SITE_NAME,
            "applicationCategory": "BusinessApplication", "applicationSubCategory": "Property management",
            "operatingSystem": "Web browser; installable on Android and iOS as a web app",
            "description": _("Rent collection and property management software for Kenyan landlords, agents and "
                             "estates: M-Pesa Paybill and Till payments, SMS receipts, meter billing and reports."),
            "url": absolute("/"), "inLanguage": LANG, "publisher": {"@id": absolute("/#organization")},
            "audience": {"@type": "Audience", "audienceType": "Landlords, rental agents and property managers",
                         "geographicArea": {"@type": "Country", "name": "Kenya"}},
            "featureList": [str(f[1]) for f in content.FEATURES]}
    try:
        plans = _plans()
    except Exception:  # a page for visitors must never fail because the plan table could not be read
        plans = []
    prices = [float(p.monthly_price) for p in plans]
    if prices:
        node["offers"] = {"@type": "AggregateOffer", "priceCurrency": "KES", "lowPrice": min(prices),
                          "highPrice": max(prices), "offerCount": len(prices),
                          "availability": "https://schema.org/InStock", "url": absolute(reverse("growth:pricing"))}
    return node


def _organization_node():
    node = {"@type": "Organization", "@id": absolute("/#organization"), "name": SITE_NAME, "url": absolute("/"),
            "logo": {"@type": "ImageObject", "url": absolute(static("icons/icon-512.png")), "width": 512, "height": 512},
            "image": absolute(static(OG_IMAGE)), "areaServed": {"@type": "Country", "name": "Kenya"},
            "description": _("Rent software made for how Kenyan landlords collect: M-Pesa, SMS, a caretaker with a "
                             "phone, and tenants who may not have a smartphone.")}
    if getattr(settings, "SUPPORT_EMAIL", ""):
        node["contactPoint"] = {"@type": "ContactPoint", "contactType": "customer support",
                                "email": settings.SUPPORT_EMAIL, "areaServed": "KE", "availableLanguage": ["en", "sw"]}
    profiles = [u.strip() for u in getattr(settings, "SOCIAL_PROFILES", "").split(",") if u.strip()]
    if profiles:
        node["sameAs"] = profiles
    return node


def _website_node():
    return {"@type": "WebSite", "@id": absolute("/#website"), "url": absolute("/"), "name": SITE_NAME,
            "inLanguage": LANG, "publisher": {"@id": absolute("/#organization")}}


# --- the pages ------------------------------------------------------------------------------------------------


def _static_pages():
    from subscriptions import services as subscription_services
    days = subscription_services.TRIAL_DAYS
    P = Page
    return {
        "landing": P(
            title=_("NyumbaPMS: M-Pesa Rent Collection Software for Kenya"),
            description=_("Collect rent on your own Paybill or Till, match every M-Pesa payment to its unit, send SMS "
                          "receipts and reminders, and see who owes. Free %(days)s-day trial.") % {"days": days},
            path="/", priority="1.0", changefreq="weekly",
            summary=_("Rent collection and property management software for Kenyan landlords, agents and estates.")),
        "growth:pricing": P(
            title=_("Pricing in KSh by number of units · NyumbaPMS"),
            description=_("Simple KSh pricing by the number of units you manage. Every plan includes every feature, "
                          "M-Pesa rent collection and SMS receipts, with a free %(days)s-day trial first.") % {"days": days},
            path=reverse("growth:pricing"), priority="0.9", changefreq="weekly",
            breadcrumb=[(_("Pricing"), reverse("growth:pricing"))]),
        "growth:solutions": P(
            title=_("Rent Software for Kenyan Landlords and Agents · NyumbaPMS"),
            description=_("How NyumbaPMS helps Kenyan landlords, agents, estates and diaspora owners collect rent by "
                          "M-Pesa, bill water, chase arrears and keep honest records."),
            path=reverse("growth:solutions"), priority="0.8", schema="CollectionPage",
            breadcrumb=[(_("Solutions"), reverse("growth:solutions"))]),
        "growth:faq": P(
            title=_("Rent Collection and M-Pesa FAQ · NyumbaPMS"),
            description=_("Answers about NyumbaPMS: collecting rent by M-Pesa Paybill or Till, tenants, setting up, "
                          "your data and safety."),
            path=reverse("growth:faq"), priority="0.8", schema="FAQPage",
            graph=[_faq_node(content.faq_pairs())], breadcrumb=[(_("FAQ"), reverse("growth:faq"))]),
        "growth:about": P(
            title=_("About NyumbaPMS: Rent Software Built for Kenya"),
            description=_("Who NyumbaPMS is for, why we built it around M-Pesa and the caretaker with a phone, and "
                          "what we promise about your money and your data."),
            path=reverse("growth:about"), priority="0.6", schema="AboutPage",
            breadcrumb=[(_("About"), reverse("growth:about"))]),
        "growth:how_it_works": P(
            title=_("How NyumbaPMS Works: From Setup to Rent Paid"),
            description=_("From adding your units to receipting an M-Pesa payment and reading the monthly report: how "
                          "NyumbaPMS works in six steps, and what is not finished yet."),
            path=reverse("growth:how_it_works"), priority="0.7",
            breadcrumb=[(_("How it works"), reverse("growth:how_it_works"))]),
        "growth:articles": P(
            title=_("Landlord Guides for Kenya: Rent, Paybill, Meters · NyumbaPMS"),
            description=_("Short, practical articles for Kenyan landlords: collecting rent on time, Paybill or Till, "
                          "fair water billing, caretaker access and move-outs."),
            path=reverse("growth:articles"), priority="0.7", schema="CollectionPage", changefreq="weekly",
            breadcrumb=[(_("Articles"), reverse("growth:articles"))]),
        "growth:tutorials": P(
            title=_("NyumbaPMS Tutorials: First Invoice, M-Pesa, Meters"),
            description=_("Step-by-step guides: your first invoice, receiving rent on your Paybill, meter readings, "
                          "importing from a spreadsheet and inviting your caretaker."),
            path=reverse("growth:tutorials"), priority="0.7", schema="CollectionPage",
            breadcrumb=[(_("Tutorials"), reverse("growth:tutorials"))]),
        "growth:demo": P(
            title=_("Book a Free NyumbaPMS Demo · Call, Video or In Person"),
            description=_("Book a short demo of NyumbaPMS by phone, video call or in person, and see how your own "
                          "Paybill, units and tenants would look."),
            path=reverse("growth:demo"), priority="0.7", schema="ContactPage",
            breadcrumb=[(_("Book a demo"), reverse("growth:demo"))]),
        "accounts:register": P(
            title=_("Start Your Free %(days)s-Day Trial · NyumbaPMS") % {"days": days},
            description=_("Create a NyumbaPMS workspace with your phone number. No card needed. Add your units and "
                          "tenants and start collecting rent by M-Pesa."),
            path=reverse("accounts:register"), priority="0.8",
            breadcrumb=[(_("Start free trial"), reverse("accounts:register"))]),
        "support:security": P(
            title=_("Security and Your Data · NyumbaPMS"),
            description=_("How NyumbaPMS protects landlord and tenant data: separation between landlords, audit log, "
                          "backups, two-step login and where data is kept."),
            path=reverse("support:security"), priority="0.5",
            breadcrumb=[(_("Security and your data"), reverse("support:security"))]),
        "support:terms": P(
            title=_("Terms of Service · NyumbaPMS"),
            description=_("The terms for using NyumbaPMS, including your records, payments to your own Paybill or "
                          "Till, and ending your account."),
            path=reverse("support:terms"), priority="0.3", changefreq="yearly",
            breadcrumb=[(_("Terms of Service"), reverse("support:terms"))]),
        "support:privacy": P(
            title=_("Privacy Policy · NyumbaPMS"),
            description=_("What personal data NyumbaPMS holds about landlords and tenants, why, for how long, and how "
                          "to ask for it back or deleted, under Kenya's Data Protection Act."),
            path=reverse("support:privacy"), priority="0.3", changefreq="yearly",
            breadcrumb=[(_("Privacy Policy"), reverse("support:privacy"))]),
        "support:delete_account": P(
            title=_("Delete Your NyumbaPMS Account"),
            description=_("How to delete your NyumbaPMS account, what is deleted, what must be kept and for how long."),
            path=reverse("support:delete_account"), priority="0.2", changefreq="yearly",
            breadcrumb=[(_("Delete account"), reverse("support:delete_account"))]),
    }


def _article_page(slug):
    item = pages.article(slug)
    if item is None:
        return None
    path = reverse("growth:article", args=[slug])
    graph = [{"@type": "Article", "headline": str(item["title"]), "description": str(item["summary"]),
              "articleBody": " ".join(f"{h}. {b}" for h, b in item["body"]), "inLanguage": LANG,
              "mainEntityOfPage": absolute(path), "image": absolute(static(OG_IMAGE)),
              "author": {"@id": absolute("/#organization")}, "publisher": {"@id": absolute("/#organization")}}]
    return Page(title=f"{item['title']} · {SITE_NAME}", description=str(item["summary"]), path=path, kind="article",
                schema="Article", priority="0.6", graph=graph,
                breadcrumb=[(_("Articles"), reverse("growth:articles")), (str(item["title"]), path)])


def _tutorial_page(slug):
    item = content.tutorial(slug)
    if item is None:
        return None
    path = reverse("growth:tutorial", args=[slug])
    graph = [{"@type": "HowTo", "name": str(item["title"]), "description": str(item["summary"]), "inLanguage": LANG,
              "totalTime": f"PT{int(item['minutes'])}M",
              "step": [{"@type": "HowToStep", "position": i, "name": str(n), "text": str(t),
                        "url": f"{absolute(path)}#step-{i}"} for i, (n, t) in enumerate(item["steps"], 1)]}]
    return Page(title=f"{item['title']} · {SITE_NAME} tutorial", description=str(item["summary"]), path=path,
                kind="article", schema="HowTo", priority="0.6", graph=graph,
                breadcrumb=[(_("Tutorials"), reverse("growth:tutorials")), (str(item["title"]), path)])


def _solution_page(slug):
    item = solutions.solution(slug)
    if item is None:
        return None
    path = reverse("growth:solution", args=[slug])
    return Page(title=f"{item['title']} · {SITE_NAME}", description=str(item["description"]), path=path,
                priority="0.8", graph=[_faq_node(item["faq"])], summary=str(item["lead"]),
                breadcrumb=[(_("Solutions"), reverse("growth:solutions")), (str(item["title"]), path)])


def all_pages():
    """Every page that may be found, in sitemap order."""
    registry = _static_pages()
    ordered = [registry["landing"]] + [p for k, p in registry.items() if k != "landing"]
    ordered += [_solution_page(s["slug"]) for s in solutions.SOLUTIONS]
    ordered += [_article_page(a["slug"]) for a in pages.ARTICLES]
    ordered += [_tutorial_page(t["slug"]) for t in content.TUTORIALS]
    return ordered


def page_for(request):
    """The registered page this request is showing, or None when it is private (the default)."""
    match = getattr(request, "resolver_match", None)
    if match is None or request.method not in ("GET", "HEAD") or request.path.startswith(tuple(PRIVATE_PREFIXES)):
        return None
    name = match.view_name
    if name == "accounts:home":
        return None if request.user.is_authenticated else _static_pages()["landing"]
    if name == "growth:article":
        return _article_page(match.kwargs.get("slug"))
    if name == "growth:tutorial":
        return _tutorial_page(match.kwargs.get("slug"))
    if name == "growth:solution":
        return _solution_page(match.kwargs.get("slug"))
    return _static_pages().get(name)


def breadcrumb_node(page):
    items = [(_("Home"), "/")] + list(page.breadcrumb)
    return {"@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i, "name": str(n), "item": absolute(p)} for i, (n, p) in enumerate(items, 1)]}


def structured_data(page):
    nodes = []
    if page.path == "/":
        nodes += [_organization_node(), _website_node(), _software_node(),
                  _faq_node(content.faq_pairs()[:6])]
    else:
        nodes.append({"@type": page.schema if page.schema not in ("Article", "HowTo", "FAQPage") else "WebPage",
                      "@id": absolute(page.path) + "#webpage", "url": absolute(page.path), "name": str(page.title),
                      "description": str(page.description), "inLanguage": LANG,
                      "isPartOf": {"@id": absolute("/#website")}, "publisher": {"@id": absolute("/#organization")}})
        if page.breadcrumb:
            nodes.append(breadcrumb_node(page))
        if page.path == reverse("growth:pricing"):
            nodes.append(_software_node())
    nodes += page.graph
    return dump({"@context": "https://schema.org", "@graph": nodes})


def context(request):
    """Template context for the page head. `seo.index` is False for everything not registered above."""
    page = page_for(request)
    if page is None:
        return {"seo": {"index": False, "lang": LANG}}
    og_image = absolute(static(OG_IMAGE))
    return {"seo": {
        "index": True, "lang": LANG, "locale": LOCALE, "title": str(page.title), "description": str(page.description),
        "canonical": absolute(page.path), "kind": page.kind, "image": og_image, "site_name": SITE_NAME,
        "jsonld": structured_data(page),
        "google_verification": getattr(settings, "GOOGLE_SITE_VERIFICATION", ""),
        "bing_verification": getattr(settings, "BING_SITE_VERIFICATION", ""),
    }}


# --- files for crawlers ---------------------------------------------------------------------------------------


def robots_txt() -> str:
    """Public pages are open to every search and AI crawler; the private paths are closed to all of them. A named
    group replaces the `*` group in a crawler's eyes, so the private paths are repeated in each."""
    private = [f"Disallow: {p}" for p in PRIVATE_PREFIXES]
    lines = ["# NyumbaPMS: rent software for Kenyan landlords. Public pages are open to search engines and AI",
             "# assistants. A summary for AI readers is at " + absolute("/llms.txt"), ""]
    lines += ["User-agent: *", "Allow: /"] + private + [""]
    for bot in SEARCH_BOTS + AI_SEARCH_BOTS:
        lines += [f"User-agent: {bot}", "Allow: /"] + private + [""]
    lines += [f"Sitemap: {absolute('/sitemap.xml')}", ""]
    return "\n".join(lines)


def sitemap_xml() -> str:
    rows = []
    for page in all_pages():
        url = absolute(page.path)
        rows.append(f"<url><loc>{url}</loc><xhtml:link rel=\"alternate\" hreflang=\"{LANG}\" href=\"{url}\"/>"
                    f"<changefreq>{page.changefreq}</changefreq><priority>{page.priority}</priority></url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">'
            + "".join(rows) + "</urlset>")


def llms_txt() -> str:
    """The llmstxt.org summary: what the site is, and the pages worth reading, for AI assistants answering
    questions such as "what software do Kenyan landlords use to collect rent by M-Pesa?"."""
    from subscriptions import services as subscription_services
    days = subscription_services.TRIAL_DAYS
    out = [f"# {SITE_NAME}", "",
           _("> Rent collection and property management software for landlords, agents and estates in Kenya. "
             "Tenants pay rent by M-Pesa to the landlord's own Paybill or Till; each payment is matched to its unit "
             "and receipted by SMS. Prices are in Kenya shillings, by number of units, with every feature on every "
             "plan and a free %(days)s-day trial.") % {"days": days}, "",
           _("NyumbaPMS never holds rent. Landlords keep their own Paybill or Till. It also bills water and "
             "electricity from meter readings (offline on a phone), sends SMS reminders and late-fee proposals, makes "
             "demand letters and notices with a service record, and gives agents and owners separate views."),
           "", "## " + _("Product"), ""]
    registry = _static_pages()
    for key in ("landing", "growth:pricing", "growth:how_it_works", "growth:about", "growth:demo",
                "accounts:register"):
        p = registry[key]
        out.append(f"- [{p.title}]({absolute(p.path)}): {p.summary or p.description}")
    out += ["", "## " + _("Solutions for Kenyan landlords"), ""]
    out += [f"- [{s['title']}]({absolute(reverse('growth:solution', args=[s['slug']]))}): {s['description']}"
            for s in solutions.SOLUTIONS]
    out += ["", "## " + _("Guides"), ""]
    out += [f"- [{a['title']}]({absolute(reverse('growth:article', args=[a['slug']]))}): {a['summary']}"
            for a in pages.ARTICLES]
    out += [f"- [{t['title']}]({absolute(reverse('growth:tutorial', args=[t['slug']]))}): {t['summary']}"
            for t in content.TUTORIALS]
    out += ["", "## " + _("Trust and legal"), ""]
    for key in ("growth:faq", "support:security", "support:privacy", "support:terms"):
        p = registry[key]
        out.append(f"- [{p.title}]({absolute(p.path)}): {p.description}")
    out += ["", "## " + _("Optional"), "", f"- [{_('Full text of this site for AI readers')}]({absolute('/llms-full.txt')})", ""]
    return "\n".join(out)


def llms_full_txt() -> str:
    """The public pages' words in one Markdown file, so an assistant can quote them without crawling each page."""
    out = [llms_txt().split("## ")[0].rstrip(), ""]
    out += ["## " + _("About"), "", str(pages.ABOUT["intro"]), ""]
    out += [f"- {w}" for w in pages.ABOUT["why"]]
    out += ["", "## " + _("What it does"), ""]
    out += [f"- **{t}**: {d}" for _icon, t, d in content.FEATURES]
    out += ["", "## " + _("How it works"), ""]
    out += [f"{i}. **{t}**: {d}" for i, (t, d) in enumerate(pages.HOW_STEPS, 1)]
    for s in solutions.SOLUTIONS:
        out += ["", f"## {s['h1']}", "", str(s["lead"]), ""]
        for heading, text, points in s["sections"]:
            out += [f"### {heading}", "", str(text), ""] + [f"- {p}" for p in points] + [""]
        out += [f"**{q}** {a}" for q, a in s["faq"]]
    out += ["", "## " + _("Frequently asked questions"), ""]
    for heading, items in content.FAQ:
        out += [f"### {heading}", ""]
        for q, a in items:
            out += [f"**{q}**", "", str(a), ""]
    out += ["## " + _("Guides"), ""]
    for a in pages.ARTICLES:
        out += [f"### {a['title']}", "", str(a["summary"]), ""] + [f"- **{h}.** {b}" for h, b in a["body"]] + [""]
    out += ["## " + _("Tutorials"), ""]
    for t in content.TUTORIALS:
        out += [f"### {t['title']}", "", str(t["summary"]), ""]
        out += [f"{i}. **{n}.** {d}" for i, (n, d) in enumerate(t["steps"], 1)] + [""]
    return "\n".join(str(x) for x in out) + "\n"
