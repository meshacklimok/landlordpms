from django.urls import path

from . import views

app_name = "support"

urlpatterns = [
    path("status/", views.StatusView.as_view(), name="status"),
    path("security/", views.SecurityView.as_view(), name="security"),
    path("help/", views.HelpIndexView.as_view(), name="help"),
    path("help/contact/", views.ContactView.as_view(), name="contact"),
    path("help/<slug:slug>/", views.HelpTopicView.as_view(), name="help_topic"),
]
