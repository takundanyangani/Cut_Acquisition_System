from datetime import timedelta
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import Notification
from apps.core.testing.factories import World
from apps.requisitions.models import AuditEntry, Requisition, State, verify_chain


class AuditChainTests(TestCase):
    def setUp(self):
        self.w = World()
        self.req = self.w.draft()
        self.w.act(self.req, self.w.requester, "submit")
        self.w.act(self.req, self.w.pa, "pa_forward")
        self.w.act(self.req, self.w.director, "director_forward", comment="Original comment")

    def test_chain_is_intact(self):
        self.assertEqual(verify_chain(self.req), [])
        seqs = list(self.req.audit_entries.values_list("seq", flat=True))
        self.assertEqual(seqs, [1, 2, 3])

    def test_entries_cannot_be_edited_or_deleted_through_the_orm(self):
        entry = self.req.audit_entries.last()
        entry.comment = "Changed"
        with self.assertRaises(ValidationError):
            entry.save()
        with self.assertRaises(ValidationError):
            entry.delete()

    def test_tampering_in_the_database_is_detected(self):
        entry = self.req.audit_entries.get(seq=3)
        AuditEntry.objects.filter(pk=entry.pk).update(comment="Rewritten later")
        problems = verify_chain(self.req)
        self.assertTrue(any("altered" in p for p in problems))
        with self.assertRaises(CommandError):
            call_command("verify_audit_chain", stdout=StringIO())

    def test_deleted_entry_is_detected(self):
        AuditEntry.objects.filter(requisition=self.req, seq=2)._raw_delete(AuditEntry.objects.db)
        problems = verify_chain(self.req)
        self.assertTrue(any("missing" in p for p in problems))

    def test_command_passes_on_clean_data(self):
        out = StringIO()
        call_command("verify_audit_chain", stdout=out)
        self.assertIn("intact", out.getvalue())


class ReminderTests(TestCase):
    def test_overdue_request_reminds_current_desk_once(self):
        w = World()
        req = w.draft()
        w.act(req, w.requester, "submit")
        Requisition.objects.filter(pk=req.pk).update(state_due_at=timezone.now() - timedelta(days=3))
        before = Notification.objects.filter(recipient=w.pa).count()
        call_command("send_reminders", "--force", stdout=StringIO())
        self.assertEqual(Notification.objects.filter(recipient=w.pa).count(), before + 1)
        call_command("send_reminders", "--force", stdout=StringIO())
        self.assertEqual(Notification.objects.filter(recipient=w.pa).count(), before + 1)

    def test_follow_up_reminder(self):
        w = World()
        req = w.eqr_to_approval()
        w.act(req, w.director, "director_approve")
        w.act(req, w.tech, "send_to_procurement", procurement_reference="PR-1")
        Requisition.objects.filter(pk=req.pk).update(next_follow_up_on=timezone.localdate())
        call_command("send_reminders", "--force", stdout=StringIO())
        self.assertTrue(w.tech.notifications.filter(title__startswith="Follow up").exists())


class RetentionTests(TestCase):
    @override_settings(ACQ_RETENTION_DAYS={"EQR": 183, "RPL": 365, "ALC": 365})
    def test_closed_requests_move_to_archive_after_retention(self):
        w = World()
        old = w.draft()
        w.act(old, w.requester, "submit")
        w.act(old, w.requester, "cancel", comment="Not needed")
        recent = w.draft()
        w.act(recent, w.requester, "submit")
        w.act(recent, w.requester, "cancel", comment="Not needed")
        Requisition.objects.filter(pk=old.pk).update(closed_at=timezone.now() - timedelta(days=200))
        call_command("apply_retention", stdout=StringIO())
        old.refresh_from_db()
        recent.refresh_from_db()
        self.assertIsNotNone(old.archived_at)
        self.assertIsNone(recent.archived_at)
        self.assertEqual(old.state, State.CANCELLED)
        self.assertTrue(Requisition.objects.filter(pk=old.pk).exists())

    def test_open_requests_are_never_archived(self):
        w = World()
        req = w.draft()
        w.act(req, w.requester, "submit")
        Requisition.objects.filter(pk=req.pk).update(created_at=timezone.now() - timedelta(days=900))
        call_command("apply_retention", stdout=StringIO())
        req.refresh_from_db()
        self.assertIsNone(req.archived_at)


@override_settings(DEBUG=True)
class SeedDemoTests(TestCase):
    def test_seed_runs_the_whole_workflow(self):
        out = StringIO()
        call_command("seed_demo", stdout=out)
        self.assertIn("Demo data loaded", out.getvalue())
        self.assertEqual(Requisition.objects.filter(state=State.COMPLETED).count(), 2)
        for req in Requisition.objects.exclude(reference__isnull=True):
            self.assertEqual(verify_chain(req), [], req.reference)
        with self.assertRaises(CommandError):
            call_command("seed_demo", stdout=StringIO())
