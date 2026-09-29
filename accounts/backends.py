from django.contrib.auth.backends import ModelBackend

from core.phone import InvalidPhoneNumber, normalize_phone

from .models import User


class PhoneOrEmailBackend(ModelBackend):
    """Log in with a phone number (any Kenyan format) or an email address."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        identifier = (username or kwargs.get("phone") or "").strip()
        if not identifier or password is None:
            return None
        user = None
        if "@" in identifier:
            user = User.objects.filter(email__iexact=identifier).first()
        else:
            try:
                user = User.objects.filter(phone=normalize_phone(identifier)).first()
            except InvalidPhoneNumber:
                return None
        if user is None:
            # Same work as a real check, so timing doesn't reveal which accounts exist.
            User().set_password(password)
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
