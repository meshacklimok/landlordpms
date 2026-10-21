from django.urls import path

from . import views

app_name = "esign"

urlpatterns = [
    path("lease/<uuid:public_id>/send/", views.SendView.as_view(), name="send"),
    path("<uuid:public_id>/", views.DetailView.as_view(), name="detail"),
    path("<uuid:public_id>/pdf/", views.PdfView.as_view(), name="pdf"),
    path("<uuid:public_id>/evidence/", views.EvidenceView.as_view(), name="evidence"),
]
