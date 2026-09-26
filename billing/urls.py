from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("charge-types/", views.ChargeTypeListView.as_view(), name="charge_types"),
]
