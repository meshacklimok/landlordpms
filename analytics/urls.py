from django.urls import path

from . import views

app_name = "console"

urlpatterns = [
    path("", views.OverviewView.as_view(), name="overview"),
    path("revenue/", views.RevenueView.as_view(), name="revenue"),
    path("payments/", views.BehaviourView.as_view(), name="behaviour"),
    path("usage/", views.UsageView.as_view(), name="usage"),
    path("organizations/", views.OrganizationsView.as_view(), name="organizations"),
    path("organizations/watchlist/", views.WatchlistView.as_view(), name="watchlist"),
    path("organizations/export/", views.OrganizationsExportView.as_view(), name="organizations_export"),
    path("organizations/<uuid:public_id>/", views.OrganizationView.as_view(), name="organization"),
    path("organizations/<uuid:public_id>/control/", views.OrganizationControlView.as_view(), name="organization_control"),
    path("control/announcements/", views.AnnouncementsView.as_view(), name="announcements"),
    path("control/email/", views.EmailRoutingView.as_view(), name="email"),
    path("control/audit/", views.AuditLogView.as_view(), name="audit"),
    path("growth/", views.GrowthView.as_view(), name="growth"),
    path("channels/", views.ChannelsView.as_view(), name="channels"),
    path("money/", views.MoneyView.as_view(), name="money"),
    path("money/costs/", views.CostsView.as_view(), name="costs"),
    path("money/<int:pk>/", views.ExpenseEditView.as_view(), name="expense"),
    path("health/", views.HealthView.as_view(), name="health"),
    path("team/", views.TeamView.as_view(), name="team"),
    path("team/add/", views.TeamAddView.as_view(), name="team_add"),
    path("team/<int:pk>/", views.TeamEditView.as_view(), name="team_edit"),
]
