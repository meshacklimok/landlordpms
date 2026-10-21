from django.urls import path

from . import views

app_name = "subscriptions"

urlpatterns = [
    path("", views.SubscriptionView.as_view(), name="page"),
    path("pay/mpesa/", views.PayMpesaView.as_view(), name="pay_mpesa"),
    path("pay/paystack/", views.PayPaystackView.as_view(), name="pay_paystack"),
    path("pay/paystack/mpesa/", views.PayPaystackMpesaView.as_view(), name="pay_paystack_mpesa"),
    path("pay/status/<uuid:public_id>/", views.PayStatusView.as_view(), name="pay_status"),
    path("pay/paystack/return/", views.PaystackReturnView.as_view(), name="paystack_return"),
    path("sms/top-up/", views.SmsTopUpView.as_view(), name="sms_top_up"),
    path("sender-name/", views.SenderNameView.as_view(), name="sender_name"),
    path("lapse-notify/", views.LapseNotifyView.as_view(), name="lapse_notify"),
    path("invoices/<uuid:public_id>/pdf/", views.InvoicePdfView.as_view(), name="invoice_pdf"),
    path("receipts/<uuid:public_id>/pdf/", views.ReceiptPdfView.as_view(), name="receipt_pdf"),
]
