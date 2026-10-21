from django.urls import path

from . import desk_views, views

app_name = "owner_portal"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("accounts/<uuid:public_id>/", views.AccountView.as_view(), name="account"),
    path("accounts/<uuid:public_id>/live/", views.LiveView.as_view(), name="live"),
    path("accounts/<uuid:public_id>/statement/", views.StatementView.as_view(), name="statement"),
    path("accounts/<uuid:public_id>/payments/", views.PaymentsView.as_view(), name="payments"),
    path("accounts/<uuid:public_id>/desk/", desk_views.DeskView.as_view(), name="desk"),
    path("accounts/<uuid:public_id>/desk/send/", desk_views.SendView.as_view(), name="desk_send"),
    path("accounts/<uuid:public_id>/desk/approvals/<uuid:approval_id>/", desk_views.AnswerView.as_view(),
         name="desk_answer"),
    path("accounts/<uuid:public_id>/desk/payout-details/", desk_views.DetailView.as_view(), name="desk_detail"),
    path("accounts/<uuid:public_id>/desk/payout-request/", desk_views.RequestView.as_view(), name="desk_request"),
    path("accounts/<uuid:public_id>/desk/payout-request/withdraw/", desk_views.RequestWithdrawView.as_view(),
         name="desk_request_withdraw"),
    path("accounts/<uuid:public_id>/people/", desk_views.GuestsView.as_view(), name="guests"),
    path("accounts/<uuid:public_id>/people/remove/", desk_views.GuestRemoveView.as_view(),
         name="guest_remove"),
    path("statements/<uuid:public_id>/pdf/", views.SentPdfView.as_view(), name="sent_pdf"),
    path("invite/<str:token>/", views.AcceptView.as_view(), name="accept"),
    # Staff, from the owner page
    path("owners/<uuid:public_id>/invite/", views.InviteView.as_view(), name="invite"),
    path("owners/<uuid:public_id>/invite/cancel/", views.RevokeInvitationView.as_view(), name="invite_revoke"),
    path("owners/<uuid:public_id>/revoke/", views.RevokeAccountView.as_view(), name="revoke"),
    path("owners/<uuid:public_id>/desk/", desk_views.StaffDeskView.as_view(), name="staff_desk"),
    path("owners/<uuid:public_id>/desk/reply/", desk_views.StaffReplyView.as_view(), name="staff_reply"),
    path("owners/<uuid:public_id>/desk/ask/", desk_views.StaffAskView.as_view(), name="staff_ask"),
    path("owners/<uuid:public_id>/desk/approvals/<uuid:approval_id>/withdraw/",
         desk_views.StaffWithdrawView.as_view(), name="staff_withdraw"),
    path("owners/<uuid:public_id>/desk/payout-details/<uuid:detail_id>/", desk_views.StaffDetailView.as_view(),
         name="staff_detail"),
    path("owners/<uuid:public_id>/desk/payout-requests/<uuid:request_id>/", desk_views.StaffRequestView.as_view(),
         name="staff_request"),
]
