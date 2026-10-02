from django.urls import path

from . import views

app_name = "maintenance"

urlpatterns = [
    path("", views.RequestListView.as_view(), name="list"),
    path("new/", views.RequestCreateView.as_view(), name="create"),
    path("photos/<uuid:public_id>/", views.PhotoView.as_view(), name="photo"),
    path("<uuid:public_id>/", views.RequestDetailView.as_view(), name="detail"),
    path("<uuid:public_id>/cost/", views.CostView.as_view(), name="cost"),
]
