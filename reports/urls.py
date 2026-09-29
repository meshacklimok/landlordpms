from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("dashboard.csv", views.DashboardExportView.as_view(), name="dashboard_export"),
    path("owner-statement/", views.OwnerStatementView.as_view(), name="owner_statement"),
    path("owner-statement.pdf", views.OwnerStatementPdfView.as_view(), name="owner_statement_pdf"),
    path("owner-statement/remit/", views.RemittanceCreateView.as_view(), name="remittance_create"),
    path("owner-statement/send/", views.StatementSendView.as_view(), name="statement_send"),
    path("owner-statement/sent/<uuid:public_id>.pdf", views.StatementSendPdfView.as_view(),
         name="statement_send_pdf"),
    path("remittances/<uuid:public_id>/void/", views.RemittanceVoidView.as_view(), name="remittance_void"),
    path("owners/<uuid:public_id>/", views.OwnerAccountView.as_view(), name="owner_account"),
    path("income/", views.IncomeView.as_view(), name="income"),
    path("income/tax-residence/", views.TaxResidenceView.as_view(), name="tax_residence"),
    path("income/<int:year>/<slug:kind>/", views.IncomeExportView.as_view(), name="income_export"),
]
