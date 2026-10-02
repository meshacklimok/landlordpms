from django.urls import path

from . import views

app_name = "banking"

urlpatterns = [
    path("accounts/", views.BankAccountsView.as_view(), name="accounts"),
    path("import/", views.ImportView.as_view(), name="import"),
    path("import/<uuid:public_id>/", views.ImportDetailView.as_view(), name="import_detail"),
    path("inbox/", views.InboxView.as_view(), name="inbox"),
    path("lines/", views.LineListView.as_view(), name="lines"),
    path("lines/<uuid:public_id>/", views.LineDetailView.as_view(), name="line"),
]
