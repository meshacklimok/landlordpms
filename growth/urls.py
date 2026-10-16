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
    path("tutorials/",views.TutorialListView.as_view(), name="tutorials"),
    path("tutorials/<slug:slug>/", views.TutorialView.as_view(), name="tutorial"),
    path("demo/", views.DemoView.as_view(), name="demo"),
    path("demo/thanks/", views.DemoThanksView.as_view(), name="demo_thanks"),
    path("refer/", views.ReferralsView.as_view(), name="refer"),
    path("robots.txt", views.RobotsView.as_view(), name="robots"),
    path("sitemap.xml", views.SitemapView.as_view(), name="sitemap"),
]
