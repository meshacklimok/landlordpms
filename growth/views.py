"""Public pages that help a landlord decide (landing, pricing, FAQ, tutorials, demo) and the Refer page (D-133)."""

from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, HttpResponsePermanentRedirect
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View

from accounts import demo
from accounts.mixins import CapabilityRequiredMixin
from core import ratelimit
from core.contacts import support_whatsapp
from subscriptions import services as subscription_services

from . import ads, articles, content, pages, referrals, seo, services, solutions
from .forms import DemoRequestForm
from .models import Article

DEMO_DONE_KEY = "demo_done"
DEMO_ENTER_PER_HOUR = 30  # sign-ins to the live demo from one connection


def _plans():
    plans = list(subscription_services.self_serve_plans().filter(monthly_price__gt=0))
    for plan in plans:
        plan.offer_list = plan.offers()
    return plans


def _plan_context():
    plans = _plans()
    return {"plans": plans, "best_saving": subscription_services.best_yearly_saving(plans)}


def landing(request):
    """What a visitor sees at "/" before signing in."""
    return render(request, "growth/landing.html", {
        **_plan_context(), "trial_days": subscription_services.trial_days(), "features": content.FEATURES,
        "facts": content.LANDING_FACTS, "audiences": content.LANDING_AUDIENCES, "blocks": content.LANDING_BLOCKS,
        "more": content.LANDING_MORE, "faq": content.faq_pairs()[:6], "swaps": content.LANDING_SWAPS,
        "works_with": content.LANDING_WORKS_WITH, "solutions": solutions.SOLUTIONS,
        "invited": referrals.invite_offer(request),
        "support_whatsapp": support_whatsapp(),
        "wa_text": quote(_("Hello, I would like to know more about NyumbaPMS."))})


class PricingView(View):
    def get(self, request):
        return render(request, "growth/pricing.html", {
            **_plan_context(), "trial_days": subscription_services.trial_days(), "features": content.FEATURES,
            "sms_price": subscription_services.sms_price(),
            "vat_registered": getattr(settings, "PLATFORM_VAT_REGISTERED", False)})


class FaqView(View):
    def get(self, request):
        return render(request, "growth/faq.html", {"groups": content.FAQ, "pairs": content.faq_pairs()})


class AboutView(View):
    def get(self, request):
        return render(request, "growth/about.html", {"about": pages.ABOUT})


class HowItWorksView(View):
    def get(self, request):
        return render(request, "growth/how_it_works.html", {
            "steps": pages.HOW_STEPS, "planning": pages.PLANNING, "roadmap": pages.ROADMAP})


class ArticleListView(View):
    def get(self, request):
        rows = articles.published()
        chosen = request.GET.get("category", "")
        if chosen in Article.Category.values:
            rows = rows.filter(category=chosen)
        else:
            chosen = ""
        q = request.GET.get("q", "").strip()[:80]
        if q:
            rows = rows.filter(Q(title__icontains=q) | Q(summary__icontains=q) | Q(body__icontains=q))
        rows = list(rows)
        # The "start here" piece gets the wide card, but only on the plain list.
        lead = rows[0] if rows and rows[0].featured and not (chosen or q) else None
        counts = {c: n for c, n in articles.published().values_list("category").annotate(n=Count("id"))}
        return render(request, "growth/articles.html", {
            "articles": rows[1:] if lead else rows, "lead": lead, "chosen": chosen, "q": q,
            "found": len(rows), "total": sum(counts.values()),
            "categories": [(value, label, counts[value]) for value, label in Article.Category.choices
                           if counts.get(value)]})


class ArticleView(View):
    def get(self, request, slug):
        item = articles.get_published(slug)
        if item is None:
            raise Http404
        return render(request, "growth/article.html", article_context(item))


def article_context(item, preview=False):
    html, toc = articles.render(item.body)
    return {"article": item, "article_html": html, "toc": toc if len(toc) > 2 else [], "preview": preview,
            "others": articles.related(item), "share_url": f"{settings.SITE_URL}{reverse('growth:article', args=[item.slug])}"}


class TutorialListView(View):
    def get(self, request):
        return render(request, "growth/tutorials.html", {"tutorials": content.TUTORIALS})


class TutorialView(View):
    def get(self, request, slug):
        item = content.tutorial(slug)
        if item is None:
            raise Http404
        others = [t for t in content.TUTORIALS if t["slug"] != slug]
        return render(request, "growth/tutorial.html", {"tutorial": item, "others": others})


class DemoView(View):
    template_name = "growth/demo.html"

    def get(self, request):
        return render(request, self.template_name, {"form": DemoRequestForm()})

    def post(self, request):
        form = DemoRequestForm(request.POST)
        if form.is_valid() and not ratelimit.hit(services.ip_key(request), services.PER_IP_HOUR, 3600):
            form.add_error(None, _("Too many requests from this connection. Please try again in an hour or call us."))
        elif form.is_valid():
            if form.cleaned_data.get("website"):  # a script filled the hidden field: look pleased, save nothing
                return redirect("growth:demo_thanks")
            item, _created = services.book_demo(form.cleaned_data, request)
            request.session[DEMO_DONE_KEY] = item.full_name.split()[0][:40]
            ads.remember_event(request, ads.LEAD)
            return redirect("growth:demo_thanks")
        return render(request, self.template_name, {"form": form})


class DemoEnterView(View):
    """"Try the demo": signs the visitor in as the sample landlord, with nothing to type (D-210)."""

    def post(self, request):
        if not ratelimit.hit(f"demo-enter:{services.ip_key(request)}", DEMO_ENTER_PER_HOUR, 3600):
            messages.error(request, _("Too many demo sign-ins from this connection. Please try again in an hour."))
            return redirect("growth:demo")
        if request.user.is_authenticated and not demo.is_demo(request):
            return redirect("accounts:home")  # a real landlord is already signed in: leave their session alone
        if not demo.enter(request):
            messages.info(request, _("The live demo is not ready yet. Book a demo and we will show you around."))
            return redirect("growth:demo")
        return redirect("accounts:home")


class DemoSwitchView(View):
    """Looks around the demo as another sample person: the owner, a manager, an accountant, a caretaker (D-210)."""

    def get(self, request, key):
        if request.user.is_authenticated and not demo.is_demo(request):
            return redirect("accounts:home")
        if not demo.switch(request, key):
            messages.info(request, _("The live demo is not ready yet. Book a demo and we will show you around."))
            return redirect("growth:demo")
        return redirect("portal:home" if key == demo.TENANT_KEY else "accounts:home")


class DemoLeaveView(View):
    """Ends the demo session and opens sign-up: from inside the demo the sign-up page would just bounce back."""

    def get(self, request):
        demo.leave(request)
        return redirect("accounts:register")


class DemoThanksView(View):
    def get(self, request):
        return render(request, "growth/demo_thanks.html", {"first_name": request.session.get(DEMO_DONE_KEY, "")})


class ReferralsView(CapabilityRequiredMixin, View):
    required_capability = "organization.manage"
    template_name = "growth/referrals.html"

    def get(self, request):
        return render(request, self.template_name, referrals.summary(request.organization))


class SolutionListView(View):
    def get(self, request):
        return render(request, "growth/solutions.html", {"solutions": solutions.SOLUTIONS})


class SolutionView(View):
    def get(self, request, slug):
        item = solutions.solution(slug)
        if item is None:
            raise Http404
        return render(request, "growth/solution.html", {
            "s": item, "related": [solutions.solution(r) for r in item["related"]],
            "tutorial": content.tutorial(item["tutorial"]), "article": articles.get_published(item.get("article", ""))})


class RobotsView(View):
    def get(self, request):
        return HttpResponse(seo.robots_txt(), content_type="text/plain; charset=utf-8")


class SitemapView(View):
    def get(self, request):
        return HttpResponse(seo.sitemap_xml(), content_type="application/xml")


class LlmsView(View):
    """/llms.txt and /llms-full.txt: a plain-text summary of the public site for AI assistants (llmstxt.org)."""

    full = False

    def get(self, request):
        body = seo.llms_full_txt() if self.full else seo.llms_txt()
        return HttpResponse(body, content_type="text/plain; charset=utf-8")


class FaviconView(View):
    """Crawlers and old browsers ask for /favicon.ico whatever the page head says."""

    def get(self, request):
        return HttpResponsePermanentRedirect(static("icons/favicon-32.png"))


class IndexNowKeyView(View):
    """Proves to Bing and Yandex (IndexNow) that this site owns the key set in INDEXNOW_KEY."""

    def get(self, request, key):
        wanted = getattr(settings, "INDEXNOW_KEY", "")
        if not wanted or key != wanted:
            raise Http404
        return HttpResponse(wanted, content_type="text/plain; charset=utf-8")
