"""Public pages that help a landlord decide (landing, pricing, FAQ, tutorials, demo) and the Refer page (D-133)."""

from django.conf import settings
from django.http import Http404, HttpResponse, HttpResponsePermanentRedirect
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.utils.translation import gettext as _
from django.views import View

from accounts.mixins import CapabilityRequiredMixin
from core import ratelimit
from subscriptions import services as subscription_services

from . import ads, content, pages, referrals, seo, services, solutions
from .forms import DemoRequestForm

DEMO_DONE_KEY = "demo_done"


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
        "invited": referrals.invite_offer(request)})


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
        return render(request, "growth/articles.html", {"articles": pages.ARTICLES})


class ArticleView(View):
    def get(self, request, slug):
        item = pages.article(slug)
        if item is None:
            raise Http404
        return render(request, "growth/article.html", {
            "article": item, "others": [a for a in pages.ARTICLES if a["slug"] != slug]})


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
            "tutorial": content.tutorial(item["tutorial"]), "article": pages.article(item.get("article", ""))})


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
