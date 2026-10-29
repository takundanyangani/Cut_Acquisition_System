"""Sign-in against the CUT staff directory. Loaded only when LDAP_SERVER_URI
is set; needs `pip install -r requirements-ldap.txt`."""

from django_auth_ldap.backend import LDAPBackend


class DirectoryBackend(LDAPBackend):
    """Match directory accounts to local accounts by email address.

    Staff can type their email or, with a suitable LDAP_USER_FILTER, their
    staff number. Either way the local account is found by the directory's
    mail attribute, so roles granted in this system stay attached and no
    duplicate accounts appear.
    """

    def get_or_build_user(self, username, ldap_user):
        model = self.get_user_model()
        mail = (ldap_user.attrs.get("mail") or [""])[0].strip().lower()
        if not mail:
            raise ldap_user.AuthenticationFailed("The directory account has no mail attribute")
        try:
            return model.objects.get(email__iexact=mail), False
        except model.DoesNotExist:
            return model(email=mail), True
