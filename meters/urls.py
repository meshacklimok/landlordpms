from django.urls import path

from . import views

app_name = "meters"

urlpatterns = [
    path("", views.MeterListView.as_view(), name="list"),
    path("new/<uuid:property_id>/", views.MeterFormView.as_view(), name="create"),
    path("readings/", views.ApprovalView.as_view(), name="approvals"),
    path("usage/", views.UsageView.as_view(), name="usage"),
    path("round/<uuid:property_id>/", views.RoundView.as_view(), name="round"),
    path("round/<uuid:property_id>/offline/", views.OfflineRoundView.as_view(), name="offline_round"),
    path("round/<uuid:property_id>/import/", views.RoundImportView.as_view(), name="round_import"),
    path("photos/<uuid:public_id>/", views.PhotoView.as_view(), name="photo"),
    path("readings/<uuid:public_id>/shares/", views.SharesView.as_view(), name="shares"),
    path("readings/<uuid:public_id>/scan-bill/", views.BillScanView.as_view(), name="scan_bill"),
    path("<uuid:public_id>/", views.MeterDetailView.as_view(), name="detail"),
    path("<uuid:public_id>/edit/", views.MeterFormView.as_view(), name="edit"),
]
