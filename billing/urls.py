from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("charge-types/", views.ChargeTypeListView.as_view(), name="charge_types"),
    path("invoices/", views.InvoiceListView.as_view(), name="invoices"),
    path("invoices/generate/", views.GenerateView.as_view(), name="generate"),
    path("invoices/generate/review/", views.GenerateReviewView.as_view(), name="generate_review"),
    path("invoices/<uuid:public_id>/", views.InvoiceDetailView.as_view(), name="invoice"),
    path("invoices/<uuid:public_id>/pdf/", views.InvoicePdfView.as_view(), name="invoice_pdf"),
    path("call-list/", views.CallListView.as_view(), name="call_list"),
    path("call-list/<uuid:public_id>/", views.FollowUpView.as_view(), name="follow_up"),
    path("arrears/", views.ArrearsView.as_view(), name="arrears"),
    path("late-fees/", views.LateFeeListView.as_view(), name="late_fees"),
    path("leases/<uuid:public_id>/", views.LeaseAccountView.as_view(), name="lease_account"),
    path("leases/<uuid:public_id>/deposit-entries/<int:entry_id>/evidence/", views.DepositEvidenceView.as_view(),
         name="deposit_evidence"),
]
