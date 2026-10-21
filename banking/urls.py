from django.urls import path

from . import views, web_api

app_name = "banking"

urlpatterns = [
    path("accounts/", views.BankAccountsView.as_view(), name="accounts"),
    path("import/", views.ImportView.as_view(), name="import"),
    path("import/<uuid:public_id>/", views.ImportDetailView.as_view(), name="import_detail"),
    path("import/<uuid:public_id>/columns/", views.ImportMapView.as_view(), name="import_map"),
    path("web-api/import/", web_api.ImportDataView.as_view(), name="web_import"),
    path("web-api/import/<uuid:public_id>/", web_api.ImportDetailDataView.as_view(), name="web_import_detail"),
    path("web-api/import/<uuid:public_id>/columns/", web_api.ImportMapDataView.as_view(), name="web_import_map"),
    path("web-api/inbox/", web_api.InboxDataView.as_view(), name="web_inbox"),
    path("inbox/", views.InboxView.as_view(), name="inbox"),
    path("lines/", views.LineListView.as_view(), name="lines"),
    path("lines/<uuid:public_id>/", views.LineDetailView.as_view(), name="line"),
]
