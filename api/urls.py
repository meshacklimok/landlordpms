from django.urls import path

from . import key_views, views, webhook_views

app_name = "api"

urlpatterns = [
    path("api/v1/me/", views.MeView.as_view(), name="me"),
    path("api/v1/<slug:resource>/", views.ResourceView.as_view(), name="resource"),
    path("api/v1/<slug:resource>/<uuid:public_id>/", views.ItemView.as_view(), name="item"),
    path("staff/api-keys/", key_views.KeyListView.as_view(), name="keys"),
    path("staff/api-keys/<uuid:public_id>/revoke/", key_views.KeyRevokeView.as_view(), name="revoke"),
    path("staff/webhooks/", webhook_views.WebhookListView.as_view(), name="webhooks"),
    path("staff/webhooks/<uuid:public_id>/", webhook_views.WebhookDetailView.as_view(), name="webhook"),
    path("staff/webhooks/<uuid:public_id>/<str:action>/", webhook_views.WebhookActionView.as_view(),
         name="webhook_action"),
    path("staff/webhooks/deliveries/<uuid:public_id>/redeliver/", webhook_views.RedeliverView.as_view(),
         name="redeliver"),
]
