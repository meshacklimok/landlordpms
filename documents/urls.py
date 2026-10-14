from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("", views.DocumentListView.as_view(), name="list"),
    path("upload/", views.UploadView.as_view(), name="upload"),
    path("<uuid:public_id>/", views.DocumentDetailView.as_view(), name="detail"),
    path("<uuid:public_id>/file/", views.FileView.as_view(), name="file"),
]
