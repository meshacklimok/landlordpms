from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("", views.HomeView.as_view(), name="home"),
    path("register/", views.RegisterView.as_view(), name="register"),
    path("verify-phone/", views.VerifyPhoneView.as_view(), name="verify_phone"),
    path("verify-email/", views.VerifyEmailView.as_view(), name="verify_email"),
    path("account/email/", views.EmailView.as_view(), name="email"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("login/code/", views.LoginMFAView.as_view(), name="login_mfa"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("account/security/", views.SecurityView.as_view(), name="security"),
    path("password-reset/", views.PasswordResetRequestView.as_view(), name="password_reset"),
    path("password-reset/confirm/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("welcome/", views.OnboardingView.as_view(), name="onboarding"),
    path("switch/", views.SwitchOrganizationView.as_view(), name="switch_org"),
    path("organization/", views.OrganizationSettingsView.as_view(), name="organization"),
    path("organization/branches/", views.BranchListView.as_view(), name="branches"),
    path("organization/branches/<uuid:public_id>/", views.BranchEditView.as_view(), name="branch"),
    path("staff/",views.StaffListView.as_view(), name="staff"),
    path("staff/two-step/", views.RequireMFAView.as_view(), name="require_mfa"),
    path("staff/invite/", views.InviteStaffView.as_view(), name="invite"),
    path("staff/invitations/<uuid:public_id>/revoke/", views.RevokeInvitationView.as_view(), name="revoke_invite"),
    path("staff/<uuid:public_id>/", views.MemberDetailView.as_view(), name="member"),
    path("invite/<str:token>/", views.AcceptInvitationView.as_view(), name="accept_invite"),
    path("roles/", views.RoleListView.as_view(), name="roles"),
    path("roles/new/", views.RoleEditView.as_view(), name="role_create"),
    path("roles/<uuid:public_id>/", views.RoleEditView.as_view(), name="role_edit"),
    path("roles/<uuid:public_id>/clone/", views.RoleCloneView.as_view(), name="role_clone"),
    path("roles/<uuid:public_id>/archive/", views.RoleArchiveView.as_view(), name="role_archive"),
]
