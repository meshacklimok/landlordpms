from django.urls import path

from portal import views as portal_views

from . import views

app_name = "tenants"

urlpatterns = [
    path("", views.TenantListView.as_view(), name="list"),
    path("new/", views.TenantCreateView.as_view(), name="create"),
    path("<uuid:public_id>/", views.TenantDetailView.as_view(), name="detail"),
    path("<uuid:public_id>/edit/", views.TenantEditView.as_view(), name="edit"),
    path("<uuid:public_id>/assign/", views.TenantAssignView.as_view(), name="assign"),
    path("<uuid:public_id>/archive/", views.TenantArchiveView.as_view(), name="archive"),
    path("<uuid:public_id>/portal/invite/", portal_views.InviteView.as_view(), name="portal_invite"),
    path("<uuid:public_id>/portal/cancel-invite/", portal_views.RevokeInvitationView.as_view(),
         name="portal_revoke_invite"),
    path("<uuid:public_id>/portal/remove/", portal_views.RevokeAccountView.as_view(), name="portal_revoke"),
    path("<uuid:public_id>/ussd-pin/remove/", portal_views.RemoveUssdPinView.as_view(), name="ussd_pin_remove"),
]
