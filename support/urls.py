from django.urls import path

from . import pwa, views

app_name = "support"

urlpatterns = [
    path("manifest.webmanifest", pwa.ManifestView.as_view(), name="manifest"),
    path("sw.js", pwa.ServiceWorkerView.as_view(), name="sw"),
    path("offline/", pwa.OfflineView.as_view(), name="offline"),
    path("status/", views.StatusView.as_view(), name="status"),
    path("security/", views.SecurityView.as_view(), name="security"),
    path("terms/", views.TermsView.as_view(), name="terms"),
    path("privacy/", views.PrivacyView.as_view(), name="privacy"),
    path("delete-account/", views.DeleteAccountView.as_view(), name="delete_account"),
    path("terms/accept/", views.AcceptTermsView.as_view(), name="accept_terms"),
    path("help/", views.HelpIndexView.as_view(), name="help"),
    path("help/contact/", views.ContactView.as_view(), name="contact"),
    path("help/<slug:slug>/", views.HelpTopicView.as_view(), name="help_topic"),
]
