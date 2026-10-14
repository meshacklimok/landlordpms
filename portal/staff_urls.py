from django.urls import path

from . import staff_views as views

app_name = "conversations"

urlpatterns = [
    path("", views.InboxView.as_view(), name="inbox"),
    path("leases/<uuid:public_id>/", views.ThreadView.as_view(), name="thread"),
    path("leases/<uuid:public_id>/offer/", views.OfferCreateView.as_view(), name="offer_create"),
    path("offers/<uuid:public_id>/withdraw/", views.OfferWithdrawView.as_view(), name="offer_withdraw"),
    path("offers/<uuid:public_id>/draft/", views.OfferDraftView.as_view(), name="offer_draft"),
]
