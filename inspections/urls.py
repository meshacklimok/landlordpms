from django.urls import path

from . import views

app_name = "inspections"

urlpatterns = [
    path("start/<uuid:public_id>/<slug:kind>/", views.StartView.as_view(), name="start"),
    path("<uuid:public_id>/", views.ReportView.as_view(), name="report"),
    path("photos/<uuid:public_id>/", views.PhotoView.as_view(), name="photo"),
    path("units/<uuid:public_id>/items/", views.RegisterView.as_view(), name="register"),
]
