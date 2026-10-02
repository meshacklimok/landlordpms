from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    path("", views.SubscriptionView.as_view(), name="page"),
    path("invoices/<uuid:public_id>/pdf/", views.InvoicePdfView.as_view(), name="invoice_pdf"),
    path("receipts/<uuid:public_id>/pdf/", views.ReceiptPdfView.as_view(), name="receipt_pdf"),
]
