import importlib.util
from unittest import skipUnless

from django.test import TestCase

from apps.accounts.models import Role, RoleGrant, User

HAS_LDAP = importlib.util.find_spec("django_auth_ldap") is not None


class FakeLdapUser:
    class AuthenticationFailed(Exception):
        pass

    def __init__(self, mail):
        self.attrs = {"mail": [mail]} if mail else {}


@skipUnless(HAS_LDAP, "django-auth-ldap is not installed (optional, see requirements-ldap.txt)")
class DirectoryBackendTests(TestCase):
    def setUp(self):
        from apps.accounts.ldap_backend import DirectoryBackend

        self.backend = DirectoryBackend()

    def test_existing_account_found_by_mail_whatever_was_typed(self):
        user = User.objects.create_user(email="joy.moyo@example.ac.zw", first_name="J", last_name="M")
        RoleGrant.objects.create(user=user, role=Role.TECHNICIAN)
        found, built = self.backend.get_or_build_user("CUT1234", FakeLdapUser("Joy.Moyo@Example.ac.zw"))
        self.assertFalse(built)
        self.assertEqual(found.pk, user.pk)

    def test_new_account_is_built_with_lowercase_email(self):
        found, built = self.backend.get_or_build_user("jmoyo", FakeLdapUser("New.Person@Example.ac.zw"))
        self.assertTrue(built)
        self.assertEqual(found.email, "new.person@example.ac.zw")

    def test_directory_entry_without_mail_is_refused(self):
        with self.assertRaises(FakeLdapUser.AuthenticationFailed):
            self.backend.get_or_build_user("jmoyo", FakeLdapUser(""))
