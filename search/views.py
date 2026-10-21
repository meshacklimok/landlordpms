from dataclasses import asdict

from django.http import JsonResponse
from django.shortcuts import render
from django.views import View

from accounts.mixins import OrgMemberRequiredMixin

from . import services


class SearchView(OrgMemberRequiredMixin, View):
    """Every match the member may see, grouped, with a link to each group's own list."""

    template_name = "search/results.html"

    def get(self, request):
        results = services.search(request.membership, request.GET.get("q"), limit=services.PAGE_LIMIT)
        return render(request, self.template_name, {
            "results": results, "q": results.q, "min_length": services.MIN_LENGTH,
            "scoped": services.scoped(request.membership),
        })


class SuggestView(OrgMemberRequiredMixin, View):
    """The first few matches in each group, as JSON, for the box in the top bar."""

    def get(self, request):
        results = services.search(request.membership, request.GET.get("q"))
        response = JsonResponse({
            "q": results.q,
            "groups": [{"key": g.key, "title": str(g.title), "more": g.more, "list_url": g.list_url,
                        "hits": [asdict(h) for h in g.hits]} for g in results.groups],
        })
        response["Cache-Control"] = "private, no-store"
        return response
