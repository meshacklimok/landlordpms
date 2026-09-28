from django.urls import path

from . import views

app_name = "mpesa"

urlpatterns = [
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("settings/<uuid:public_id>/", views.AccountSettingsView.as_view(), name="account"),
    path("inbox/", views.InboxView.as_view(), name="inbox"),
    path("transactions/", views.TransactionListView.as_view(), name="transactions"),
    path("transactions/<str:trans_id>/", views.TransactionDetailView.as_view(), name="transaction"),
    path("request/<uuid:public_id>/", views.RequestPaymentView.as_view(), name="request"),
]
