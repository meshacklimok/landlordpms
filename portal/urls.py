from django.urls import path

from . import views

app_name = "portal"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("leases/<uuid:public_id>/", views.LeaseView.as_view(), name="lease"),
    path("leases/<uuid:public_id>/repairs/new/", views.RepairCreateView.as_view(), name="repair_create"),
    path("repairs/<uuid:public_id>/", views.RepairView.as_view(), name="repair"),
    path("repairs/photos/<uuid:public_id>/", views.RepairPhotoView.as_view(), name="repair_photo"),
    path("receipts/<uuid:public_id>/", views.ReceiptView.as_view(), name="receipt"),
    path("invite/<str:token>/", views.AcceptView.as_view(), name="accept"),
]
