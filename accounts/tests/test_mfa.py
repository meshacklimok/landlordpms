"""Two-step login with an authenticator app (D-059)."""

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from accounts import mfa
from accounts.capabilities import CAPABILITY_MAP
from accounts.models import RecoveryCode, TOTPDevice
from accounts.permissions import effective_capabilities
from accounts.tests.factories import PASSWORD, add_member, make_org, make_user
from audit.models import AuditEvent

pytestmark = pytest.mark.django_db


def code(secret, ahead=0):
    return mfa.code_at(secret, mfa.current_step() + ahead)


def enable(user):
    """Switches MFA on with last step's code, so this step's and the next are still free to use."""
    mfa.start_setup(user)
    secret = mfa.pending_secret(user)
    codes = mfa.confirm_setup(user, code(secret, -1))
    return secret, codes


def password_step(client, user, **extra):
    return client.post(reverse("accounts:login"), {"identifier": user.phone, "password": PASSWORD, **extra})


def test_totp_matches_the_rfc_6238_vector():
    # RFC 6238 appendix B, SHA-1, T=59s: 94287082 (8 digits), so 287082 in 6.
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert mfa.code_at(secret, mfa.current_step(59)) == "287082"
    assert mfa._match_step(secret, "287082", now=59 + 30) == 1  # one step of drift allowed
    assert mfa._match_step(secret, "287082", now=59 + 90) is None


def test_setting_up():
    user = make_user()
    mfa.start_setup(user)
    secret = mfa.pending_secret(user)
    assert TOTPDevice.objects.get(user=user).secret != secret  # stored encrypted
    assert not mfa.is_enabled(user)
    with pytest.raises(ValidationError):
        mfa.confirm_setup(user, "000000" if code(secret) != "000000" else "111111")
    codes = mfa.confirm_setup(user, code(secret))
    assert mfa.is_enabled(user) and len(codes) == 10 and len(set(codes)) == 10
    assert not RecoveryCode.objects.filter(code_hash__in=codes).exists()  # stored hashed
    assert AuditEvent.objects.filter(action="mfa.enable", object_id=str(user.public_id)).exists()
    with pytest.raises(ValidationError):
        mfa.start_setup(user)
    uri = mfa.provisioning_uri(user, secret)
    assert uri.startswith("otpauth://totp/landlordpms%3A") and f"secret={secret}" in uri
    assert mfa.qr_svg(uri).startswith("<svg")


def test_a_code_works_once_and_recovery_codes_work_once():
    user = make_user()
    secret, codes = enable(user)
    now = code(secret)
    assert mfa.check(user, now) == "totp"
    assert mfa.check(user, now) is None  # replayed
    assert mfa.check(user, codes[0].upper()) == "recovery"
    assert mfa.check(user, codes[0]) is None
    assert mfa.remaining_recovery_codes(user) == 9


def test_wrong_codes_are_limited():
    user = make_user()
    secret, _codes = enable(user)
    for _ in range(mfa.FAIL_LIMIT):
        assert mfa.check(user, "not-a-code") is None
    with pytest.raises(mfa.MFAError):
        mfa.check(user, code(secret))
    assert AuditEvent.objects.filter(action="mfa.failed").count() == mfa.FAIL_LIMIT


def test_new_recovery_codes_replace_the_old():
    user = make_user()
    secret, old = enable(user)
    with pytest.raises(ValidationError):
        mfa.regenerate_recovery_codes(user, "bad")
    new = mfa.regenerate_recovery_codes(user, code(secret))
    assert mfa.check(user, old[0]) is None and mfa.check(user, new[0]) == "recovery"


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_login_asks_for_the_code_after_the_password(client):
    user = make_user()
    secret, _codes = enable(user)
    response = password_step(client, user, next="/reports/")
    assert response.url == reverse("accounts:login_mfa")
    assert "_auth_user_id" not in client.session  # not logged in yet
    assert client.get(reverse("accounts:home")).status_code == 302
    page = client.post(reverse("accounts:login_mfa"), {"code": "123"})
    assert "That code is wrong" in page.content.decode()
    response = client.post(reverse("accounts:login_mfa"), {"code": code(secret)})
    assert response.url == "/reports/"
    assert client.session["_auth_user_id"] == str(user.pk) and client.session[mfa.VERIFIED]
    event = AuditEvent.objects.get(action="user.login")
    assert event.changes == {"mfa": [None, "totp"]}


def test_login_without_mfa_is_one_step(client):
    user = make_user()
    make_org(owner=user)
    assert password_step(client, user).url == reverse("accounts:home")
    assert client.session["_auth_user_id"] == str(user.pk) and not client.session.get(mfa.VERIFIED)


def test_the_second_step_expires(client, monkeypatch):
    user = make_user()
    enable(user)
    password_step(client, user)
    later = mfa.time.time() + mfa.PENDING_TTL + 1
    monkeypatch.setattr(mfa.time, "time", lambda: later)
    response = client.post(reverse("accounts:login_mfa"), {"code": "123456"})
    assert response.url == reverse("accounts:login") and "_auth_user_id" not in client.session


def test_a_recovery_code_logs_in_and_warns(client):
    user = make_user()
    make_org(owner=user)
    _secret, codes = enable(user)
    password_step(client, user)
    response = client.post(reverse("accounts:login_mfa"), {"code": codes[3]}, follow=True)
    assert "You used a recovery code. 9 left" in response.content.decode()


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def test_switching_on_and_off_ends_other_sessions(client, monkeypatch):
    # Mid-step and frozen, so the codes below cannot drift out of the window if a 30-second step ends mid-test.
    monkeypatch.setattr(mfa.time, "time", lambda: 1_790_000_025.0)
    user = make_user()
    make_org(owner=user)
    other = Client()
    other.login(username=user.phone, password=PASSWORD)
    client.login(username=user.phone, password=PASSWORD)
    assert other.get(reverse("accounts:home")).status_code == 200

    client.post(reverse("accounts:security"), {"action": "start"})
    secret = mfa.pending_secret(user)
    page = client.post(reverse("accounts:security"), {"action": "confirm", "code": code(secret, -1)})
    assert "Your recovery codes" in page.content.decode()
    assert other.get(reverse("accounts:home")).status_code == 302  # logged out
    assert client.get(reverse("accounts:home")).status_code == 200  # this one kept

    other = Client()
    password_step(other, user)
    other.post(reverse("accounts:login_mfa"), {"code": code(secret)})
    assert other.get(reverse("accounts:home")).status_code == 200
    page = client.post(reverse("accounts:security"), {"action": "disable", "off-password": "wrong",
                                                      "off-code": code(secret, 1)})
    assert "The password is wrong" in page.content.decode() and mfa.is_enabled(user)
    client.post(reverse("accounts:security"), {"action": "disable", "off-password": PASSWORD,
                                               "off-code": code(secret, 1)})
    assert not mfa.is_enabled(user) and not RecoveryCode.objects.filter(user=user).exists()
    assert other.get(reverse("accounts:home")).status_code == 302
    assert client.get(reverse("accounts:home")).status_code == 200


def test_the_security_page(client):
    m = make_org()
    client.login(username=m.user.phone, password=PASSWORD)
    assert "Set up two-step login" in client.get(reverse("accounts:security")).content.decode()
    client.post(reverse("accounts:security"), {"action": "start"})
    page = client.get(reverse("accounts:security")).content.decode()
    assert "<svg" in page and mfa.pending_secret(m.user) in page
    assert client.get(reverse("accounts:home")).content.decode().count(reverse("accounts:security")) >= 1


def test_the_home_page_suggests_mfa_to_roles_with_sensitive_powers(client):
    m = make_org()
    assert mfa.suggest(m)
    client.login(username=m.user.phone, password=PASSWORD)
    assert "Protect your account with two-step login" in client.get(reverse("accounts:home")).content.decode()
    enable(m.user)
    assert not mfa.suggest(m)
    # A role with no sensitive capability is not nagged.
    viewer = add_member(m.organization, "viewer")
    assert mfa.suggest(viewer) == any(CAPABILITY_MAP[c].sensitive for c in effective_capabilities(viewer))


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


def test_admin_needs_a_platform_admin_with_a_verified_session(client):
    admin_url = reverse("admin:index")
    assert client.get(admin_url).url.startswith(reverse("accounts:login"))
    assert client.get(reverse("admin:login")).url.startswith(reverse("accounts:login"))

    member = make_org()
    client.login(username=member.user.phone, password=PASSWORD)
    assert client.get(admin_url).status_code == 404

    staff = make_user(is_staff=True, is_superuser=True)
    client.login(username=staff.phone, password=PASSWORD)
    assert client.get(admin_url).url == reverse("accounts:security")
    secret, _codes = enable(staff)
    # A session from before MFA was switched on must pass a code first.
    client = Client()
    client.login(username=staff.phone, password=PASSWORD)
    response = client.get(admin_url)
    assert response.url.startswith(reverse("accounts:login_mfa"))
    response = client.post(reverse("accounts:login_mfa"), {"code": code(secret), "next": admin_url})
    assert response.url == admin_url
    assert client.get(admin_url).status_code == 200


def test_a_platform_admin_can_reset_a_users_mfa(client):
    staff = make_user(is_staff=True, is_superuser=True)
    secret, _codes = enable(staff)
    password_step(client, staff)
    client.post(reverse("accounts:login_mfa"), {"code": code(secret)})
    user = make_user()
    enable(user)
    theirs = Client()
    theirs.login(username=user.phone, password=PASSWORD)
    response = client.post(reverse("admin:accounts_user_changelist"),
                           {"action": "reset_mfa", "_selected_action": [user.pk]})
    assert response.status_code == 302
    assert not mfa.is_enabled(user)
    assert AuditEvent.objects.filter(action="mfa.admin_reset", actor=staff, object_id=str(user.public_id)).exists()
    assert theirs.get(reverse("accounts:security")).status_code == 302  # their session ended
    with pytest.raises(ValidationError):
        mfa.admin_reset(user, staff)
