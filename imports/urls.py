from django.urls import path

from . import views

app_name = "imports"

urlpatterns = [
    path("", views.ImportListView.as_view(), name="list"),
    path("<slug:kind>/upload/", views.ImportUploadView.as_view(), name="upload"),
    path("<slug:kind>/template.csv", views.ImportTemplateView.as_view(), name="template"),
    path("<uuid:public_id>/", views.ImportDetailView.as_view(), name="detail"),
]
