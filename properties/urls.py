from django.urls import path

from . import views

app_name = "properties"

urlpatterns = [
    path("", views.PropertyListView.as_view(), name="list"),
    path("new/", views.PropertyCreateView.as_view(), name="create"),
    path("<uuid:public_id>/", views.PropertyDetailView.as_view(), name="detail"),
    path("<uuid:public_id>/edit/", views.PropertyEditView.as_view(), name="edit"),
    path("<uuid:public_id>/archive/", views.PropertyArchiveView.as_view(), name="archive"),
    path("<uuid:public_id>/buildings/new/", views.BuildingCreateView.as_view(), name="building_create"),
    path("<uuid:public_id>/units/new/", views.UnitCreateView.as_view(), name="unit_create"),
    path("buildings/<uuid:public_id>/", views.BuildingEditView.as_view(), name="building_edit"),
    path("units/", views.UnitListView.as_view(), name="units"),
    path("units/<uuid:public_id>/", views.UnitDetailView.as_view(), name="unit"),
]
