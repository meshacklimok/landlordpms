from django.urls import path

from . import views

app_name = "leases"

urlpatterns = [
    path("", views.LeaseListView.as_view(), name="list"),
    path("new/<uuid:public_id>/", views.LeaseCreateView.as_view(), name="create"),
    path("<uuid:public_id>/", views.LeaseDetailView.as_view(), name="detail"),
    path("<uuid:public_id>/edit/", views.LeaseEditView.as_view(), name="edit"),
]
