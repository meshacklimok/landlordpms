from django.urls import path

from . import views

app_name = "prospects"

urlpatterns = [
    path("", views.ProspectListView.as_view(), name="list"),
    path("new/", views.ProspectCreateView.as_view(), name="create"),
    path("<uuid:public_id>/", views.ProspectDetailView.as_view(), name="detail"),
    path("<uuid:public_id>/edit/", views.ProspectEditView.as_view(), name="edit"),
    path("<uuid:public_id>/tenant/", views.ConvertView.as_view(), name="convert"),
]
