from django.urls import path

from . import views

app_name = "search"

urlpatterns = [
    path("", views.SearchView.as_view(), name="results"),
    path("suggest/", views.SuggestView.as_view(), name="suggest"),
]
