from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import Holiday, OutboundEmail, Sequence
from apps.core.notify import deliver, notify
from apps.core.testing.factories import World
from apps.core.workdays import add_working_days, working_days_between

HARARE = ZoneInfo("Africa/Harare")


class WorkdayTests(TestCase):
    def test_weekends_are_skipped(self):
        friday = date(2026, 10, 30)
        self.assertEqual(add_working_days(friday, 1), date(2026, 11, 2))
        self.assertEqual(working_days_between(friday, date(2026, 11, 2)), 1)

    def test_holidays_are_skipped(self):
        Holiday.objects.create(date=date(2026, 12, 22), name="Unity Day")
        Holiday.objects.create(date=date(2026, 12, 25), name="Christmas Day")
        self.assertEqual(add_working_days(date(2026, 12, 21), 1), date(2026, 12, 23))
        self.assertEqual(add_working_days(date(2026, 12, 24), 1), date(2026, 12, 28))

    def test_datetime_keeps_time_of_day(self):
        start = datetime(2026, 10, 30, 15, 0, tzinfo=HARARE)
        due = add_working_days(start, 1)
        self.assertEqual((due.weekday(), due.hour), (0, 15))


class SequenceTests(TestCase):
    def test_counter_per_key_and_year(self):
        self.assertEqual(Sequence.next_value("EQR", 2026), 1)
        self.assertEqual(Sequence.next_value("EQR", 2026), 2)
        self.assertEqual(Sequence.next_value("EQR", 2027), 1)
        self.assertEqual(Sequence.next_value("RPL", 2026), 1)


class OutboxTests(TestCase):
    def test_notify_queues_and_sends(self):
        w = World()
        with self.captureOnCommitCallbacks(execute=True):
            notify([w.requester, w.requester, None], "Hello", "Body", "/requests/")
        self.assertEqual(w.requester.notifications.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/requests/", mail.outbox[0].body)
        self.assertEqual(OutboundEmail.objects.get().status, OutboundEmail.Status.SENT)

    @override_settings(ACQ_EMAIL_DELIVERY="cron")
    def test_failed_email_is_retried(self):
        w = World()
        notify([w.requester], "Hello", "Body", "/")
        with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("SMTP down")):
            self.assertEqual(deliver(), 0)
        message = OutboundEmail.objects.get()
        self.assertEqual(message.status, OutboundEmail.Status.PENDING)
        self.assertEqual(message.attempts, 1)
        self.assertIn("SMTP down", message.last_error)
        self.assertEqual(deliver(), 1)
        self.assertEqual(OutboundEmail.objects.get().status, OutboundEmail.Status.SENT)

    def test_notification_link_cannot_redirect_offsite(self):
        w = World()
        notify([w.requester], "x", "", "https://evil.example.com/", email=False)
        self.client.force_login(w.requester)
        note = w.requester.notifications.get()
        response = self.client.get(reverse("core:open_notification", args=[note.pk]))
        self.assertEqual(response["Location"], "/")


class ErrorPageAndHeaderTests(TestCase):
    def test_custom_404_for_anonymous_and_signed_in(self):
        response = self.client.get("/no-such-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Not on file", status_code=404)
        self.assertContains(response, "Sign in", status_code=404)
        w = World()
        self.client.force_login(w.requester)
        response = self.client.get("/no-such-page/")
        self.assertContains(response, "Go to the dashboard", status_code=404)

    def test_500_template_renders_without_database(self):
        from apps.core.views import server_error

        response = server_error(None)
        self.assertEqual(response.status_code, 500)
        self.assertIn(b"Something went wrong", response.content)

    def test_security_headers(self):
        response = self.client.get(reverse("accounts:login"))
        csp = response["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_csrf_failure_page(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse("accounts:login"), {"username": "a", "password": "b"})
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "form had expired", status_code=403)

    def test_health(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.json(), {"status": "ok"})
