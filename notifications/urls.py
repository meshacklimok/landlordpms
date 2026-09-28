from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.MessageLogView.as_view(), name="log"),
    path("inbox/", views.InboxView.as_view(), name="inbox"),
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("templates/", views.TemplateListView.as_view(), name="templates"),
    path("templates/<slug:type_codename>/<str:channel>/<str:language>/", views.TemplateEditView.as_view(),
         name="template_edit"),
    path("announcements/", views.AnnouncementListView.as_view(), name="announcements"),
    path("announcements/new/", views.AnnouncementNewView.as_view(), name="announcement_new"),
    path("announcements/<uuid:public_id>/", views.AnnouncementDetailView.as_view(), name="announcement"),
    path("tenants/<uuid:public_id>/<str:channel>/", views.TenantChannelView.as_view(), name="tenant_channel"),
    path("<uuid:public_id>/", views.MessageDetailView.as_view(), name="detail"),
]
