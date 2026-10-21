"""Adding people to the developer console and choosing what they see (D-135)."""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from accounts.models import User
from audit import services as audit

from . import access
from .models import PlatformStaff


def _check_grant(actor, role: str, capabilities: list[str]) -> list[str]:
    """What the seat gets: the ticked pages, or the role's usual ones when none are ticked.
    Nobody can give more than they hold, so a team manager cannot make an owner."""
    mine = set(access.allowed(actor))
    if "staff" not in mine:
        raise PermissionDenied("staff")
    chosen = [c for c in (capabilities or access.ROLE_PRESETS[role]) if c in access.CAPABILITIES]
    if not set(chosen) <= mine:
        raise ValidationError(_("You can only give access to pages you can open yourself."))
    if role == PlatformStaff.Role.OWNER and not actor.is_superuser:
        raise ValidationError(_("Only an owner can make another owner."))
    return chosen


@transaction.atomic
def add(actor, *, phone: str, role: str, capabilities: list[str], note: str = "", request=None) -> PlatformStaff:
    chosen = _check_grant(actor, role, capabilities)
    user = User.objects.filter(phone=phone, is_active=True).first()
    if user is None:
        raise ValidationError(_("No one has signed up with that number yet. Ask them to register first."))
    if PlatformStaff.objects.filter(user=user).exists():
        raise ValidationError(_("They are already on the console team."))
    seat = PlatformStaff.objects.create(user=user, role=role, capabilities=chosen, note=note, added_by=actor)
    audit.record("console.staff_added", actor=actor, obj=seat, request=request,
                 changes={"role": [None, role], "capabilities": [None, chosen]})
    return seat


@transaction.atomic
def update(actor, seat: PlatformStaff, *, role: str, capabilities: list[str], note: str = "", is_active: bool = True,
           request=None) -> PlatformStaff:
    if seat.user_id == actor.pk:
        raise ValidationError(_("You cannot change your own access. Ask another owner."))
    chosen = _check_grant(actor, role, capabilities)
    before = {"role": seat.role, "capabilities": seat.capabilities, "is_active": seat.is_active}
    seat.role, seat.capabilities, seat.note, seat.is_active = role, chosen, note, is_active
    seat.save(update_fields=["role", "capabilities", "note", "is_active", "updated_at"])
    audit.record("console.staff_changed", actor=actor, obj=seat, request=request,
                 changes={k: [before[k], getattr(seat, k)] for k in before if before[k] != getattr(seat, k)})
    return seat


# --- Owner-only controls (D-137): only superusers reach these; every one is audited with a reason. ---

def _owner_only(actor):
    if not actor.is_superuser:
        raise PermissionDenied("owner")


def _reason(reason: str) -> str:
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError(_("Write the reason, so the audit trail says why."))
    return reason[:200]


def set_organization_status(actor, org, status: str, reason: str, request=None):
    from accounts.models import Organization

    _owner_only(actor)
    reason = _reason(reason)
    if status not in Organization.Status.values:
        raise ValidationError(_("Unknown status."))
    before = org.status
    if before == status:
        return org
    org.status = status
    org.save(update_fields=["status", "updated_at"])
    audit.record("console.organization_status", actor=actor, organization=org, obj=org,
                 changes={"status": [before, status], "reason": [None, reason]}, request=request)
    return org


def extend_trial(actor, org, days: int, reason: str, request=None):
    import datetime

    from django.utils import timezone

    _owner_only(actor)
    reason = _reason(reason)
    sub = getattr(org, "subscription", None)
    if sub is None:
        raise ValidationError(_("This organization has no subscription."))
    if not 1 <= days <= 90:
        raise ValidationError(_("Choose between 1 and 90 days."))
    before = sub.trial_ends_on
    base = max(before or timezone.localdate(), timezone.localdate())
    sub.trial_ends_on = base + datetime.timedelta(days=days)
    sub.save(update_fields=["trial_ends_on", "updated_at"])
    audit.record("console.trial_extended", actor=actor, organization=org, obj=sub,
                 changes={"trial_ends_on": [str(before), str(sub.trial_ends_on)], "reason": [None, reason]},
                 request=request)
    return sub


@transaction.atomic
def set_subscription(actor, org, *, plan, status: str, interval: str, period_end, reason: str, request=None):
    """Moves an organization to another plan, status, billing interval or paid-until date by hand: a free
    year for a partner, a plan fixed after a dispute. The landlord is not charged for it."""
    from subscriptions.models import Subscription

    _owner_only(actor)
    reason = _reason(reason)
    sub = getattr(org, "subscription", None)
    if sub is None:
        raise ValidationError(_("This organization has no subscription."))
    if status not in Subscription.Status.values or interval not in Subscription.Interval.values:
        raise ValidationError(_("Unknown status or interval."))
    wanted = {"plan": plan, "status": status, "interval": interval, "period_end": period_end}
    before = {k: getattr(sub, k) for k in wanted}
    changes = {k: [str(before[k]), str(v)] for k, v in wanted.items() if before[k] != v}
    if not changes:
        raise ValidationError(_("Nothing to change: those details are already on the subscription."))
    for k, v in wanted.items():
        setattr(sub, k, v)
    if status != Subscription.Status.PAST_DUE:
        sub.past_due_since = None
    if period_end and not sub.period_start:
        sub.period_start = timezone.localdate()
    sub.save()
    audit.record("console.subscription_changed", actor=actor, organization=org, obj=sub, request=request,
                 changes={**changes, "reason": [None, reason]})
    return sub


def extend_grace(actor, org, days: int, reason: str, request=None):
    """No lapse until this many days from today (a dispute, a promise to pay)."""
    _owner_only(actor)
    reason = _reason(reason)
    sub = getattr(org, "subscription", None)
    if sub is None:
        raise ValidationError(_("This organization has no subscription."))
    if not 1 <= days <= 90:
        raise ValidationError(_("Choose between 1 and 90 days."))
    before = sub.grace_until
    sub.grace_until = timezone.localdate() + datetime.timedelta(days=days)
    sub.save(update_fields=["grace_until", "updated_at"])
    audit.record("console.grace_extended", actor=actor, organization=org, obj=sub, request=request,
                 changes={"grace_until": [str(before), str(sub.grace_until)], "reason": [None, reason]})
    return sub


def sign_out_organization(actor, org, reason: str, request=None) -> int:
    """Ends every session of everyone in the organization (a suspected break-in, a staff clean-up)."""
    from accounts.models import Membership

    _owner_only(actor)
    reason = _reason(reason)
    ended = 0
    for user in User.objects.filter(pk__in=Membership.objects.filter(organization=org).values("user")):
        ended += _drop_sessions(user)
    audit.record("console.organization_signed_out", actor=actor, organization=org, obj=org, request=request,
                 changes={"sessions_ended": [None, ended], "reason": [None, reason]})
    return ended


@transaction.atomic
def set_member_active(actor, org, user, active: bool, reason: str, request=None):
    """Suspends or restores one person's seat in this organization. A suspended person is signed out."""
    from accounts.models import Membership

    _owner_only(actor)
    reason = _reason(reason)
    seat = Membership.objects.filter(user=user, organization=org).first()
    if seat is None:
        raise ValidationError(_("That person is not part of this organization."))
    if seat.is_active == active:
        raise ValidationError(_("They are already in that state."))
    seat.is_active = active
    seat.save(update_fields=["is_active", "updated_at"])
    ended = 0 if active else _drop_sessions(user)
    audit.record("console.member_active" if active else "console.member_suspended", actor=actor, organization=org,
                 obj=seat, request=request, changes={"is_active": [not active, active],
                                                    "sessions_ended": [None, ended], "reason": [None, reason]})
    return seat


def _drop_sessions(user) -> int:
    """Signs the user out everywhere, so a lost or stolen phone's sessions end with the change."""
    from django.contrib.sessions.models import Session

    n = 0
    for s in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        if str(s.get_decoded().get("_auth_user_id")) == str(user.pk):
            s.delete()
            n += 1
    return n


@transaction.atomic
def change_login_contact(actor, org, user, *, phone: str = "", email: str = "", reset_mfa: bool = False,
                         reason: str, request=None):
    """For a landlord who lost the phone number (or email) they sign in with. The Platform Owner checks who they
    are, then sets the new number and/or email. The new phone must be verified by its own code at the next sign-in;
    a new email counts as unverified until its code is entered, and works for sign-in and reset either way (D-206).
    The old number and email are told, and every session of the account ends. `reset_mfa` also removes the
    authenticator app and recovery codes (lost device too); they set it up again, as the organization may require."""
    from accounts.identity import _claim_email
    from accounts.models import Membership
    from core.phone import InvalidPhoneNumber, normalize_phone

    _owner_only(actor)
    reason = _reason(reason)
    if not Membership.objects.filter(user=user, organization=org).exists():
        raise ValidationError(_("That person is not part of this organization."))
    before = {"phone": user.phone, "email": user.email or ""}
    new_phone, new_email = (phone or "").strip(), (email or "").strip().lower()
    if not new_phone and not new_email and not reset_mfa:
        raise ValidationError(_("Give the new phone number, the new email, or tick the two-step reset."))
    changes = {}
    if new_phone:
        try:
            new_phone = normalize_phone(new_phone)
        except InvalidPhoneNumber:
            raise ValidationError(_("That is not a valid Kenyan phone number.")) from None
        if new_phone != user.phone:
            if User.objects.filter(phone=new_phone).exclude(pk=user.pk).exists():
                raise ValidationError(_("Another account already uses that phone number."))
            user.phone, user.phone_verified_at = new_phone, None
            changes["phone"] = [before["phone"], new_phone]
    if new_email and new_email != before["email"]:
        _claim_email(new_email, exclude_phone=user.phone)
        user.email, user.email_verified_at = new_email, None
        changes["email"] = [before["email"], new_email]
    if not changes and not reset_mfa:
        raise ValidationError(_("Nothing to change: those details are already on the account."))
    user.save(update_fields=["phone", "phone_verified_at", "email", "email_verified_at"])
    if reset_mfa:
        from accounts import mfa

        had = mfa.is_enabled(user)
        mfa.admin_reset(actor, user, request=request)
        changes["two_step"] = ["on" if had else "off", "reset"]
    ended = _drop_sessions(user)
    audit.record("console.login_contact_changed", actor=actor, organization=org, obj=user, request=request,
                 changes={**changes, "reason": [None, reason], "sessions_ended": [None, ended]})
    if changes.keys() & {"phone", "email"}:
        transaction.on_commit(lambda: _tell_old_contact(user, before, changes))
    return user


def _tell_old_contact(user, before: dict, changes: dict) -> None:
    """A SIM-swap or takeover warning to the details that were replaced. Never blocks the change."""
    from django.core.mail import send_mail

    from notifications.providers.sms import send_sms

    text = _("The sign-in details on your NyumbaPMS account were changed by our support team. "
             "If this was not you, contact support now.")
    try:
        if "phone" in changes:
            send_sms(before["phone"], text)
        if "email" in changes and before["email"]:
            send_mail(_("Your NyumbaPMS sign-in details changed"), text, None, [before["email"]])
        elif "phone" in changes and user.email:
            send_mail(_("Your NyumbaPMS sign-in details changed"), text, None, [user.email])
    except Exception:  # noqa: BLE001, S110 - a notice that fails to send must not undo the change
        pass


def _can_announce(actor):
    if not access.can(actor, "announce"):
        raise PermissionDenied("announce")


def _end(ends_at):
    """The end the sender chose, or one month from now."""
    from .models import PlatformAnnouncement

    return ends_at or timezone.now() + datetime.timedelta(days=PlatformAnnouncement.KEEP_DAYS)


def announce(actor, *, message: str, level: str, ends_at=None, organizations=(), request=None, starts_at=None):
    """A notice for everyone signed in, or only for the organizations given. Stays a month unless told otherwise."""
    from .models import PlatformAnnouncement

    _can_announce(actor)
    item = PlatformAnnouncement.objects.create(message=message.strip(), level=level, ends_at=_end(ends_at),
                                               starts_at=starts_at, created_by=actor)
    item.organizations.set(organizations)
    audit.record("console.announcement", actor=actor, obj=item, request=request,
                 changes={"message": [None, item.message], "to": [None, _audience(item)]})
    return item


def edit_announcement(actor, item, *, message: str, level: str, ends_at=None, organizations=(), request=None,
                      starts_at=None):
    _can_announce(actor)
    before = {"message": item.message, "level": item.level, "ends_at": str(item.ends_at), "to": _audience(item)}
    item.message, item.level, item.ends_at = message.strip(), level, _end(ends_at)
    item.starts_at = starts_at
    item.save(update_fields=["message", "level", "ends_at", "starts_at", "updated_at"])
    item.organizations.set(organizations)
    after = {"message": item.message, "level": item.level, "ends_at": str(item.ends_at), "to": _audience(item)}
    audit.record("console.announcement_edit", actor=actor, obj=item, request=request,
                 changes={k: [before[k], after[k]] for k in before if before[k] != after[k]})
    return item


def _audience(item) -> str:
    names = list(item.organizations.values_list("name", flat=True)[:20])
    return ", ".join(names) if names else "everyone"


def switch_announcement(actor, item, active: bool, request=None):
    _can_announce(actor)
    item.is_active = active
    item.save(update_fields=["is_active", "updated_at"])
    audit.record("console.announcement_on" if active else "console.announcement_off", actor=actor, obj=item,
                 request=request)
    return item


# --- The developer's expense ledger (D-138) ---

def save_expense(actor, form, request=None):
    """Saves a valid ExpenseForm (new or edited) and audits it."""
    from .models import PlatformExpense

    if "finance" not in access.allowed(actor):
        raise PermissionDenied("finance")
    new = form.instance.pk is None
    before = {}
    if not new:
        row = PlatformExpense.objects.filter(pk=form.instance.pk).values().first()
        before = {k: str(v) for k, v in row.items() if k in form.fields}
    expense = form.save(commit=False)
    if new:
        expense.recorded_by = actor
    expense.save()
    after = {k: str(getattr(expense, k)) for k in form.fields}
    audit.record("console.expense_added" if new else "console.expense_changed", actor=actor, obj=expense,
                 changes={k: [before.get(k), v] for k, v in after.items() if before.get(k) != v}, request=request)
    return expense


def delete_expense(actor, expense, request=None):
    if "finance" not in access.allowed(actor):
        raise PermissionDenied("finance")
    audit.record("console.expense_deleted", actor=actor, obj=expense,
                 changes={"amount": [str(expense.amount), None], "category": [expense.category, None]}, request=request)
    expense.delete()


def _need_articles(actor):
    if "articles" not in access.allowed(actor):
        raise PermissionDenied("articles")


@transaction.atomic
def save_article(actor, form, action: str = "save", request=None):
    """Saves a valid ArticleForm. `action` is "save" (keeps the state), "publish" or "unpublish"; publishing a
    first time dates it now, and the public sitemap and AI summary pick it up from the table."""
    _need_articles(actor)
    new = form.instance.pk is None
    was = None if new else form.instance.is_published
    article = form.save(commit=False)
    if new:
        article.created_by = actor
    if action == "publish":
        article.is_published = True
        article.published_at = article.published_at or timezone.now()
    elif action == "unpublish":
        article.is_published = False
    article.save()
    changed = {k: [None, str(getattr(article, k))[:80]] for k in ("title", "slug")} if new else {
        k: [None, str(getattr(article, k))[:80]] for k in form.changed_data if k != "body"}
    if "body" in form.changed_data:
        changed["body"] = ["changed", "changed"]
    if was != article.is_published:
        changed["is_published"] = [was, article.is_published]
    audit.record("console.article_published" if action == "publish" else "console.article_saved", actor=actor,
                 obj=article, changes=changed, request=request)
    return article


@transaction.atomic
def delete_article(actor, article, request=None):
    _need_articles(actor)
    if article.is_published:
        raise ValidationError(_("Unpublish it first. A published article may have been shared."))
    audit.record("console.article_deleted", actor=actor, obj=article,
                 changes={"title": [article.title, None]}, request=request)
    article.delete()
