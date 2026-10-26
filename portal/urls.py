from django.urls import path

from ai_assistant import portal_views

from . import views

app_name = "portal"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("leases/<uuid:public_id>/", views.LeaseView.as_view(), name="lease"),
    path("leases/<uuid:public_id>/repairs/new/", views.RepairCreateView.as_view(), name="repair_create"),
    path("leases/<uuid:public_id>/notice/", views.NoticeCreateView.as_view(), name="notice_create"),
    path("leases/<uuid:public_id>/payments/", views.PaymentsView.as_view(), name="payments"),
    path("payments/<uuid:public_id>/report/", views.PaymentReportView.as_view(), name="payment_report"),
    path("leases/<uuid:public_id>/utilities/", views.UtilitiesView.as_view(), name="utilities"),
    path("own-use/<uuid:public_id>/delete/", views.OwnUseDeleteView.as_view(), name="own_use_delete"),
    path("leases/<uuid:public_id>/messages/", views.MessagesView.as_view(), name="messages"),
    path("leases/<uuid:public_id>/agreement/", views.LeaseTermsView.as_view(), name="lease_terms"),
    path("leases/<uuid:public_id>/sign/<uuid:request_id>/", views.SigningView.as_view(), name="signing"),
    path("leases/<uuid:public_id>/sign/<uuid:request_id>/pdf/", views.SigningPdfView.as_view(), name="signing_pdf"),
    path("offers/<uuid:public_id>/answer/", views.OfferAnswerView.as_view(), name="offer_answer"),
    path("repairs/<uuid:public_id>/", views.RepairView.as_view(), name="repair"),
    path("repairs/photos/<uuid:public_id>/", views.RepairPhotoView.as_view(), name="repair_photo"),
    path("documents/<uuid:public_id>/", views.DocumentView.as_view(), name="document"),
    path("receipts/<uuid:public_id>/", views.ReceiptView.as_view(), name="receipt"),
    path("invoices/<uuid:public_id>/pdf/", views.InvoicePdfView.as_view(), name="invoice_pdf"),
    path("invite/<str:token>/", views.AcceptView.as_view(), name="accept"),
    path("no-match/", views.NoMatchView.as_view(), name="no_match"),
    path("ask/", portal_views.TenantAskView.as_view(), name="ask"),
    path("ask/confirm/", portal_views.TenantConfirmView.as_view(), name="ask_confirm"),
]
