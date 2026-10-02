from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("charge-types/", views.ChargeTypeListView.as_view(), name="charge_types"),
    path("invoices/", views.InvoiceListView.as_view(), name="invoices"),
    path("invoices/generate/", views.GenerateView.as_view(), name="generate"),
    path("invoices/<uuid:public_id>/", views.InvoiceDetailView.as_view(), name="invoice"),
    path("call-list/", views.CallListView.as_view(), name="call_list"),
    path("call-list/<uuid:public_id>/", views.FollowUpView.as_view(), name="follow_up"),
    path("arrears/", views.ArrearsView.as_view(), name="arrears"),
    path("leases/<uuid:public_id>/", views.LeaseAccountView.as_view(), name="lease_account"),
]
