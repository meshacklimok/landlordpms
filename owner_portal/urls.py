from django.urls import path

from . import views

app_name = "owner_portal"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("accounts/<uuid:public_id>/", views.AccountView.as_view(), name="account"),
    path("accounts/<uuid:public_id>/statement/", views.StatementView.as_view(), name="statement"),
    path("accounts/<uuid:public_id>/payments/", views.PaymentsView.as_view(), name="payments"),
    path("statements/<uuid:public_id>/pdf/", views.SentPdfView.as_view(), name="sent_pdf"),
    path("invite/<str:token>/", views.AcceptView.as_view(), name="accept"),
    # Staff, from the owner page
    path("owners/<uuid:public_id>/invite/", views.InviteView.as_view(), name="invite"),
    path("owners/<uuid:public_id>/invite/cancel/", views.RevokeInvitationView.as_view(), name="invite_revoke"),
    path("owners/<uuid:public_id>/revoke/", views.RevokeAccountView.as_view(), name="revoke"),
]
