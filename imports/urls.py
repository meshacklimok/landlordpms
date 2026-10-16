from django.urls import path

from . import views

app_name = "imports"

urlpatterns = [
    path("", views.ImportListView.as_view(), name="list"),
    path("<slug:kind>/upload/", views.ImportUploadView.as_view(), name="upload"),
    path("<slug:kind>/template.csv", views.ImportTemplateView.as_view(), name="template"),
    path("<slug:kind>/template.xlsx", views.ImportExcelTemplateView.as_view(), name="template_xlsx"),
    path("<slug:kind>/form.pdf", views.ImportPrintView.as_view(), name="print"),
    path("<slug:kind>/photo/", views.ImportPhotoView.as_view(), name="photo"),
    path("<uuid:public_id>/", views.ImportDetailView.as_view(), name="detail"),
]
