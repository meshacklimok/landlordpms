from django.urls import path

from . import views

app_name = "growth"

urlpatterns = [
    path("pricing/", views.PricingView.as_view(), name="pricing"),
    path("faq/", views.FaqView.as_view(), name="faq"),
    path("about/", views.AboutView.as_view(), name="about"),
    path("how-it-works/", views.HowItWorksView.as_view(), name="how_it_works"),
    path("articles/", views.ArticleListView.as_view(), name="articles"),
    path("articles/<slug:slug>/", views.ArticleView.as_view(), name="article"),
    path("solutions/", views.SolutionListView.as_view(), name="solutions"),
    path("solutions/<slug:slug>/", views.SolutionView.as_view(), name="solution"),
    path("tutorials/",views.TutorialListView.as_view(), name="tutorials"),
    path("tutorials/<slug:slug>/", views.TutorialView.as_view(), name="tutorial"),
    path("demo/", views.DemoView.as_view(), name="demo"),
    path("demo/thanks/", views.DemoThanksView.as_view(), name="demo_thanks"),
    path("demo/try/", views.DemoEnterView.as_view(), name="demo_enter"),
    path("demo/as/<slug:key>/", views.DemoSwitchView.as_view(), name="demo_as"),
    path("demo/leave/", views.DemoLeaveView.as_view(), name="demo_leave"),
    path("refer/", views.ReferralsView.as_view(), name="refer"),
    path("robots.txt", views.RobotsView.as_view(), name="robots"),
    path("sitemap.xml", views.SitemapView.as_view(), name="sitemap"),
    path("favicon.ico", views.FaviconView.as_view(), name="favicon"),
    path("llms.txt", views.LlmsView.as_view(), name="llms"),
    path("llms-full.txt", views.LlmsView.as_view(full=True), name="llms_full"),
    path("indexnow/<str:key>.txt", views.IndexNowKeyView.as_view(), name="indexnow_key"),
]
