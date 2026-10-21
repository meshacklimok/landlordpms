"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from billing.views import InvoiceCheckView
from core.csp import CspReportView
from esign.views import CheckView as SigningCheckView
from esign.views import SignPdfView, SignView
from letters.views import CheckView as LetterCheckView
from mpesa.hook_views import DarajaHookView
from mpesa.views import PayLinkStatusView, PayLinkView
from notifications.email_views import EmailStopView
from notifications.hook_views import AfricasTalkingHookView, OrgWhatsAppHookView, WhatsAppHookView
from payments.views import PublicReceiptCheckView, PublicReceiptView
from portal.ussd import UssdHookView
from properties.views import HostelStayView, VacancyView
from reports.views import MonthlyReportStopView
from subscriptions.hook_views import PlatformHookView
from support.views import HealthzView

urlpatterns = [
    path("ok-ml-staff/", admin.site.urls),
    path("healthz", HealthzView.as_view(), name="healthz"),
    # Fixed in settings.CONTENT_SECURITY_POLICY["report-uri"].
    path("csp-report/", CspReportView.as_view(), name="csp_report"),
    path("properties/", include("properties.urls")),
    path("tenants/", include("tenants.urls")),
    path("leases/", include("leases.urls")),
    path("imports/", include("imports.urls")),
    path("v/<str:token>/", VacancyView.as_view(), name="vacancy"),
    path("stay/<str:token>/", HostelStayView.as_view(), name="hostel_stay"),
    path("r/<str:token>/", PublicReceiptView.as_view(), name="receipt_link"),
    path("mr/<str:token>/", MonthlyReportStopView.as_view(), name="monthly_report_stop"),
    path("ic/<uuid:public_id>/", InvoiceCheckView.as_view(), name="invoice_check"),
    path("rc/<str:token>/", PublicReceiptCheckView.as_view(), name="receipt_check"),
    path("p/<str:token>/", PayLinkView.as_view(), name="pay_link"),
    path("l/<str:code>/", LetterCheckView.as_view(), name="letter_check"),
    path("s/<str:code>/", SigningCheckView.as_view(), name="esign_check"),
    path("sign/<str:token>/", SignView.as_view(), name="esign_sign"),
    path("sign/<str:token>/pdf/", SignPdfView.as_view(), name="esign_sign_pdf"),
    path("e/<str:token>/", EmailStopView.as_view(), name="email_stop"),
    path("p/<str:token>/<uuid:request_id>/", PayLinkStatusView.as_view(), name="pay_link_status"),
    path("hooks/sms/africastalking/<str:token>/<slug:kind>/", AfricasTalkingHookView.as_view(),
         name="hook_africastalking"),
    path("hooks/ussd/africastalking/<str:token>/", UssdHookView.as_view(), name="hook_ussd"),
    path("hooks/whatsapp/", WhatsAppHookView.as_view(), name="hook_whatsapp"),
    path("hooks/whatsapp/<str:token>/", OrgWhatsAppHookView.as_view(), name="hook_whatsapp_org"),
    path("hooks/c2b/<str:token>/<slug:kind>/", DarajaHookView.as_view(), name="hook_daraja"),
    path("hooks/plat/<str:token>/<slug:kind>/", PlatformHookView.as_view(), name="hook_platform"),
    path("billing/", include("billing.urls")),
    path("payments/", include("payments.urls")),
    path("messages/", include("notifications.urls")),
    path("mpesa/", include("mpesa.urls")),
    path("bank/", include("banking.urls")),
    path("inspections/", include("inspections.urls")),
    path("meters/", include("meters.urls")),
    path("expenses/", include("expenses.urls")),
    path("maintenance/", include("maintenance.urls")),
    path("documents/", include("documents.urls")),
    path("prospects/", include("prospects.urls")),
    path("letters/", include("letters.urls")),
    path("esign/", include("esign.urls")),
    path("reports/", include("reports.urls")),
    path("search/", include("search.urls")),
    path("ask/", include("ai_assistant.urls")),
    path("my/", include("portal.urls")),
    path("conversations/", include("portal.staff_urls")),
    path("owner/", include("owner_portal.urls")),
    path("console/", include("console.urls")),
    path("subscription/", include("subscriptions.urls")),
    path("", include("growth.urls")),
    path("", include("support.urls")),
    path("", include("api.urls")),
    path("", include("accounts.urls")),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
