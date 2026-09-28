from django.urls import path

from . import views

app_name = "mpesa"

urlpatterns = [
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("settings/<uuid:public_id>/", views.AccountSettingsView.as_view(), name="account"),
]
