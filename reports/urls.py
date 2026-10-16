from django.urls import path

from . import ops_views, views
from .xlsx import also_excel

app_name = "reports"

urlpatterns = [
    path("centre/", ops_views.ReportCentreView.as_view(), name="centre"),
    path("maintenance/", ops_views.MaintenanceReportView.as_view(), name="maintenance"),
    path("payments/", ops_views.PaymentsReportView.as_view(), name="payments"),
    path("arrears/", ops_views.ArrearsReportView.as_view(), name="arrears"),
    path("utilities/", ops_views.UtilitiesReportView.as_view(), name="utilities"),
    path("leases/", ops_views.LeasesReportView.as_view(), name="leases"),
    path("behaviour/", ops_views.PaymentBehaviourView.as_view(), name="behaviour"),
    path("scorecard/", ops_views.ScorecardView.as_view(), name="scorecard"),
    path("deposits/", ops_views.DepositsReportView.as_view(), name="deposits"),
    path("delivery/", ops_views.DeliveryReportView.as_view(), name="delivery"),
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("dashboard.csv", also_excel(views.DashboardExportView.as_view()), name="dashboard_export"),
    path("owner-statement/", views.OwnerStatementView.as_view(), name="owner_statement"),
    path("owner-statement.pdf", views.OwnerStatementPdfView.as_view(), name="owner_statement_pdf"),
    path("owner-statement/remit/", views.RemittanceCreateView.as_view(), name="remittance_create"),
    path("owner-statement/send/", views.StatementSendView.as_view(), name="statement_send"),
    path("owner-statement/sent/<uuid:public_id>.pdf", views.StatementSendPdfView.as_view(),
         name="statement_send_pdf"),
    path("remittances/<uuid:public_id>/void/", views.RemittanceVoidView.as_view(), name="remittance_void"),
    path("owners/<uuid:public_id>/", views.OwnerAccountView.as_view(), name="owner_account"),
    path("income/", views.IncomeView.as_view(), name="income"),
    path("profit-loss/", also_excel(views.ProfitLossView.as_view()), name="profit_loss"),
    path("cash-flow/", also_excel(views.CashFlowView.as_view()), name="cash_flow"),
    path("fees/", also_excel(views.FeesView.as_view()), name="fees"),
    path("units/", also_excel(views.UnitsView.as_view()), name="units"),
    path("receivables/", also_excel(views.ReceivablesView.as_view()), name="receivables"),
    path("accounting-export/", views.AccountingExportView.as_view(), name="accounting_export"),
    path("income/tax-residence/", views.TaxResidenceView.as_view(), name="tax_residence"),
    path("income/<int:year>/<slug:kind>/", also_excel(views.IncomeExportView.as_view()), name="income_export"),
]
