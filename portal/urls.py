from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("leases/<uuid:public_id>/", views.LeaseView.as_view(), name="lease"),
    path("receipts/<uuid:public_id>/", views.ReceiptView.as_view(), name="receipt"),
    path("invite/<str:token>/", views.AcceptView.as_view(), name="accept"),
]
