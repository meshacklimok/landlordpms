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

from notifications.hook_views import AfricasTalkingHookView
from payments.views import PublicReceiptView
from properties.views import VacancyView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("properties/", include("properties.urls")),
    path("tenants/", include("tenants.urls")),
    path("leases/", include("leases.urls")),
    path("imports/", include("imports.urls")),
    path("v/<str:token>/", VacancyView.as_view(), name="vacancy"),
    path("r/<str:token>/", PublicReceiptView.as_view(), name="receipt_link"),
    path("hooks/sms/africastalking/<str:token>/<slug:kind>/", AfricasTalkingHookView.as_view(),
         name="hook_africastalking"),
    path("billing/", include("billing.urls")),
    path("payments/", include("payments.urls")),
    path("messages/", include("notifications.urls")),
    path("", include("accounts.urls")),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
