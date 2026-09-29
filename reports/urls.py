from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("dashboard.csv", views.DashboardExportView.as_view(), name="dashboard_export"),
    path("owner-statement/", views.OwnerStatementView.as_view(), name="owner_statement"),
    path("owner-statement.pdf", views.OwnerStatementPdfView.as_view(), name="owner_statement_pdf"),
    path("income/", views.IncomeView.as_view(), name="income"),
    path("income/tax-residence/", views.TaxResidenceView.as_view(), name="tax_residence"),
    path("income/<int:year>/<slug:kind>/", views.IncomeExportView.as_view(), name="income_export"),
]
