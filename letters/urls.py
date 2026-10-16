from django.urls import path

from . import views

app_name = "letters"

urlpatterns = [
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("lease/<uuid:public_id>/", views.LeaseLettersView.as_view(), name="lease"),
    path("<uuid:public_id>/pdf/", views.LetterPdfView.as_view(), name="pdf"),
    path("lease/<uuid:public_id>/notices/", views.NoticesView.as_view(), name="notices"),
    path("lease/<uuid:public_id>/handover/", views.HandoverView.as_view(), name="handover"),
    path("notice/<uuid:public_id>/pdf/", views.NoticePdfView.as_view(), name="notice_pdf"),
    path("notice/<uuid:public_id>/service/", views.ServiceView.as_view(), name="notice_service"),
]
