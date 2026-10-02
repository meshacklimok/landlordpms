from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.PaymentListView.as_view(), name="list"),
    path("export.csv", views.PaymentExportView.as_view(), name="export"),
    path("review/", views.ReviewQueueView.as_view(), name="review"),
    path("record/", views.LeasePickerView.as_view(), name="pick_lease"),
    path("record/<uuid:public_id>/", views.RecordPaymentView.as_view(), name="record"),
    path("leases/<uuid:public_id>/apply-credit/", views.ApplyCreditView.as_view(), name="apply_credit"),
    path("<uuid:public_id>/", views.PaymentDetailView.as_view(), name="detail"),
    path("<uuid:public_id>/receipt/", views.ReceiptView.as_view(), name="receipt"),
]
