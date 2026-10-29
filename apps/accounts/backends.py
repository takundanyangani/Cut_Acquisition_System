from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailOrStaffNumberBackend(ModelBackend):
    """Sign in with a CUT email address or a staff number."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        identifier = (username or kwargs.get(User.USERNAME_FIELD) or "").strip()
        if not identifier or password is None:
            return None
        lookup = {"email__iexact": identifier} if "@" in identifier else {"staff_number__iexact": identifier}
        try:
            user = User._default_manager.get(**lookup)
        except User.DoesNotExist:
            # Run the hasher anyway so response time does not reveal unknown accounts.
            User().set_password(password)
            return None
        except User.MultipleObjectsReturned:
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
