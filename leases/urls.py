from django.urls import path

from . import views, web_api

app_name = "leases"

urlpatterns = [
    path("web-api/pickups/<uuid:public_id>/", web_api.PickupsDataView.as_view(), name="web_pickups"),
    path("", views.LeaseListView.as_view(), name="list"),
    path("new/<uuid:public_id>/", views.LeaseCreateView.as_view(), name="create"),
    path("<uuid:public_id>/", views.LeaseDetailView.as_view(), name="detail"),
    path("<uuid:public_id>/edit/", views.LeaseEditView.as_view(), name="edit"),
    path("<uuid:public_id>/agreement/", views.AgreementView.as_view(), name="agreement"),
    path("<uuid:public_id>/move-out/", views.MoveOutView.as_view(), name="move_out"),
    path("<uuid:public_id>/move-out/pdf/", views.MoveOutPdfView.as_view(), name="move_out_pdf"),
]
