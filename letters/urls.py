from django.urls import path

from . import views

app_name = "letters"

urlpatterns = [
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("lease/<uuid:public_id>/", views.LeaseLettersView.as_view(), name="lease"),
    path("<uuid:public_id>/pdf/", views.LetterPdfView.as_view(), name="pdf"),
]
