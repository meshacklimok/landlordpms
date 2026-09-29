from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("income/", views.IncomeView.as_view(), name="income"),
    path("income/tax-residence/", views.TaxResidenceView.as_view(), name="tax_residence"),
    path("income/<int:year>/<slug:kind>/", views.IncomeExportView.as_view(), name="income_export"),
]
