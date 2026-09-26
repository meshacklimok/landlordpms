from django.urls import path

from . import views

app_name = "tenants"

urlpatterns = [
    path("", views.TenantListView.as_view(), name="list"),
    path("new/", views.TenantCreateView.as_view(), name="create"),
    path("<uuid:public_id>/", views.TenantDetailView.as_view(), name="detail"),
    path("<uuid:public_id>/edit/", views.TenantEditView.as_view(), name="edit"),
    path("<uuid:public_id>/archive/", views.TenantArchiveView.as_view(), name="archive"),
]
