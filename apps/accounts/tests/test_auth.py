import time
from datetime import timedelta

from django.conf import settings
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Delegation, LoginAttempt, Role, RoleGrant, User
from apps.core.testing.factories import PASSWORD, World


class LoginTests(TestCase):
    def setUp(self):
        self.w = World()
        self.user = self.w.requester
        self.user.staff_number = "CUT0042"
        self.user.save()
        self.url = reverse("accounts:login")

    def test_sign_in_with_email_any_case(self):
        response = self.client.post(self.url, {"username": self.user.email.upper(), "password": PASSWORD})
        self.assertRedirects(response, reverse("core:dashboard"))

    def test_sign_in_with_staff_number(self):
        response = self.client.post(self.url, {"username": "cut0042", "password": PASSWORD})
        self.assertRedirects(response, reverse("core:dashboard"))

    def test_wrong_password_message_does_not_reveal_account(self):
        known = self.client.post(self.url, {"username": self.user.email, "password": "bad"})
        unknown = self.client.post(self.url, {"username": "nobody@test.cut.local", "password": "bad"})
        self.assertContains(known, "do not match an active account")
        self.assertContains(unknown, "do not match an active account")

    def test_lockout_after_five_failures(self):
        for _ in range(settings.ACQ_LOGIN_MAX_FAILURES):
            self.client.post(self.url, {"username": self.user.email, "password": "bad"})
        response = self.client.post(self.url, {"username": self.user.email, "password": PASSWORD})
        self.assertContains(response, "Too many failed attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_lockout_expires(self):
        for _ in range(settings.ACQ_LOGIN_MAX_FAILURES):
            self.client.post(self.url, {"username": self.user.email, "password": "bad"})
        LoginAttempt.objects.update(created_at=timezone.now() - timedelta(minutes=settings.ACQ_LOGIN_LOCK_MINUTES + 1))
        response = self.client.post(self.url, {"username": self.user.email, "password": PASSWORD})
        self.assertRedirects(response, reverse("core:dashboard"))

    @override_settings(ACQ_LOGIN_MAX_FAILURES_PER_IP=3)
    def test_ip_lockout_across_accounts(self):
        for name in ("a", "b", "c"):
            self.client.post(self.url, {"username": f"{name}@x.zw", "password": "bad"})
        response = self.client.post(self.url, {"username": self.user.email, "password": PASSWORD})
        self.assertContains(response, "Too many failed attempts")

    def test_inactive_user_cannot_sign_in(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post(self.url, {"username": self.user.email, "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_sign_out_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, self.url)

    def test_pages_need_sign_in(self):
        response = self.client.get(reverse("core:dashboard"))
        self.assertRedirects(response, f"{self.url}?next=/")


class SessionRulesTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_temporary_password_forces_change(self):
        user = self.w.requester
        user.must_change_password = True
        user.save()
        self.client.force_login(user)
        response = self.client.get(reverse("core:dashboard"))
        self.assertRedirects(response, reverse("accounts:password_change"))
        response = self.client.post(
            reverse("accounts:password_change"),
            {"old_password": PASSWORD, "new_password1": "Brand-New-Pass-77", "new_password2": "Brand-New-Pass-77"},
        )
        self.assertRedirects(response, reverse("core:dashboard"))
        user.refresh_from_db()
        self.assertFalse(user.must_change_password)
        self.assertIsNotNone(user.password_changed_at)

    def test_idle_session_is_signed_out(self):
        self.client.force_login(self.w.requester)
        self.client.get(reverse("core:dashboard"))
        session = self.client.session
        session["last_activity"] = int(time.time()) - settings.ACQ_IDLE_TIMEOUT_MINUTES * 60 - 5
        session.save()
        response = self.client.get(reverse("core:dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_password_reset_email(self):
        response = self.client.post(reverse("accounts:password_reset"), {"email": self.w.requester.email})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/accounts/password/reset/", mail.outbox[0].body)
        self.client.post(reverse("accounts:password_reset"), {"email": "unknown@test.cut.local"})
        self.assertEqual(len(mail.outbox), 1)


class RolesAndDelegationTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_has_role(self):
        self.assertTrue(self.w.director.has_role(Role.ICT_DIRECTOR))
        self.assertFalse(self.w.requester.has_role(Role.ICT_DIRECTOR))
        self.assertTrue(self.w.tech.is_ict_staff)
        self.assertFalse(self.w.requester.is_ict_staff)

    def test_delegation_view_creates_and_ends(self):
        w = self.w
        deputy = w.user("deputy", "Dee", "Deputy")
        self.client.force_login(w.director)
        start = timezone.localtime().strftime("%Y-%m-%dT%H:%M")
        end = (timezone.localtime() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(
            reverse("accounts:delegation_create"),
            {"role": Role.ICT_DIRECTOR, "delegate": deputy.pk, "starts_at": start, "ends_at": end, "reason": "Leave"},
        )
        self.assertRedirects(response, reverse("accounts:delegations"))
        delegation = Delegation.objects.get()
        deputy.clear_role_cache()
        self.assertTrue(deputy.has_role(Role.ICT_DIRECTOR))
        self.assertTrue(deputy.notifications.exists())
        self.client.post(reverse("accounts:delegation_revoke", args=[delegation.pk]))
        deputy = User.objects.get(pk=deputy.pk)
        self.assertFalse(deputy.has_role(Role.ICT_DIRECTOR))

    def test_cannot_delegate_a_role_you_do_not_hold(self):
        self.client.force_login(self.w.csm)
        response = self.client.post(
            reverse("accounts:delegation_create"),
            {
                "role": Role.ICT_DIRECTOR,
                "delegate": self.w.other.pk,
                "starts_at": "2030-01-01T08:00",
                "ends_at": "2030-01-05T08:00",
                "reason": "x",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Delegation.objects.exists())

    def test_requesters_cannot_open_delegations(self):
        self.client.force_login(self.w.requester)
        self.assertEqual(self.client.get(reverse("accounts:delegation_create")).status_code, 403)


class AdminAccessTests(TestCase):
    def test_only_superusers_reach_admin(self):
        w = World()
        RoleGrant.objects.create(user=w.director, role=Role.SYSTEM_ADMIN)
        w.director.is_staff = True
        w.director.save()
        self.client.force_login(w.director)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        admin = User.objects.create_superuser(
            email="root@test.cut.local", password=PASSWORD, first_name="R", last_name="Oot"
        )
        self.client.force_login(admin)
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        self.assertEqual(self.client.get("/admin/requisitions/requisition/").status_code, 200)
        self.assertEqual(self.client.get("/admin/accounts/user/add/").status_code, 200)

    def test_new_users_from_admin_must_change_password(self):
        admin = User.objects.create_superuser(
            email="root@test.cut.local", password=PASSWORD, first_name="R", last_name="Oot"
        )
        self.client.force_login(admin)
        response = self.client.post(
            "/admin/accounts/user/add/",
            {
                "email": "new@test.cut.local",
                "first_name": "New",
                "last_name": "Person",
                "password1": "Temp-Pass-2026x",
                "password2": "Temp-Pass-2026x",
                "role_grants-TOTAL_FORMS": "0",
                "role_grants-INITIAL_FORMS": "0",
                "role_grants-MIN_NUM_FORMS": "0",
                "role_grants-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertEqual(response.status_code, 302, response.content[:2000])
        self.assertTrue(User.objects.get(email="new@test.cut.local").must_change_password)
