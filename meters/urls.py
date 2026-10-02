from django.urls import path

from . import views

app_name = "meters"

urlpatterns = [
    path("", views.MeterListView.as_view(), name="list"),
    path("new/<uuid:property_id>/", views.MeterFormView.as_view(), name="create"),
    path("readings/", views.ApprovalView.as_view(), name="approvals"),
    path("round/<uuid:property_id>/", views.RoundView.as_view(), name="round"),
    path("photos/<uuid:public_id>/", views.PhotoView.as_view(), name="photo"),
    path("<uuid:public_id>/", views.MeterDetailView.as_view(), name="detail"),
    path("<uuid:public_id>/edit/", views.MeterFormView.as_view(), name="edit"),
]
