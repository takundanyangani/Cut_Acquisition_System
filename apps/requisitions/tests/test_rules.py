"""Controls added on top of the SOPs: separation of duties, delegation,
signatures, concurrency and cancellation."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import Delegation, LoginAttempt, Role, RoleGrant
from apps.core.testing.factories import World
from apps.requisitions import workflow
from apps.requisitions.models import Kind, State
from apps.requisitions.services import SignatureError, StaleRequisition, WorkflowError, perform


class SeparationOfDutiesTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_director_cannot_approve_own_request(self):
        w = self.w
        req = w.draft(requester=w.director)
        w.act(req, w.director, "submit")
        w.act(req, w.pa, "pa_forward")
        req.refresh_from_db()
        self.assertEqual(req.state, State.DIRECTOR_REVIEW)
        with self.assertRaisesMessage(WorkflowError, "not allowed"):
            w.act(req, w.director, "director_forward", comment="self")
        self.assertIn("ICT Director", workflow.blocked_reason(req))

    def test_delegate_can_act_and_both_names_are_recorded(self):
        w = self.w
        deputy = w.user("deputy", "Dee", "Deputy")
        Delegation.objects.create(
            delegator=w.director,
            delegate=deputy,
            role=Role.ICT_DIRECTOR,
            starts_at=timezone.now() - timedelta(hours=1),
            ends_at=timezone.now() + timedelta(days=3),
            reason="Leave",
            created_by=w.director,
        )
        req = w.draft(requester=w.director)
        w.act(req, w.director, "submit")
        w.act(req, w.pa, "pa_forward")
        req.refresh_from_db()
        self.assertEqual(workflow.blocked_reason(req), "")
        w.act(req, deputy, "director_forward", comment="Approved in the Director's absence")
        entry = req.audit_entries.get(action="director_forward")
        self.assertEqual(entry.actor, deputy)
        self.assertEqual(entry.on_behalf_of, w.director)
        self.assertEqual(entry.actor_role, Role.ICT_DIRECTOR)
        self.assertIn("acting for", entry.data["statement"])

    def test_expired_or_revoked_delegation_gives_no_power(self):
        w = self.w
        deputy = w.user("deputy", "Dee", "Deputy")
        d = Delegation.objects.create(
            delegator=w.director,
            delegate=deputy,
            role=Role.ICT_DIRECTOR,
            starts_at=timezone.now() - timedelta(days=5),
            ends_at=timezone.now() - timedelta(days=1),
            reason="Past leave",
            created_by=w.director,
        )
        self.assertFalse(deputy.has_role(Role.ICT_DIRECTOR))
        d.ends_at = timezone.now() + timedelta(days=1)
        d.revoked_at = timezone.now()
        d.save()
        deputy.clear_role_cache()
        self.assertFalse(deputy.has_role(Role.ICT_DIRECTOR))

    def test_delegation_lapses_when_delegator_loses_the_role(self):
        w = self.w
        deputy = w.user("deputy", "Dee", "Deputy")
        Delegation.objects.create(
            delegator=w.director,
            delegate=deputy,
            role=Role.ICT_DIRECTOR,
            starts_at=timezone.now() - timedelta(hours=1),
            ends_at=timezone.now() + timedelta(days=1),
            reason="Leave",
            created_by=w.director,
        )
        RoleGrant.objects.filter(user=w.director, role=Role.ICT_DIRECTOR).delete()
        self.assertFalse(deputy.has_role(Role.ICT_DIRECTOR))

    def test_technician_cannot_issue_stock_to_self(self):
        w = self.w
        laptop = w.stock()
        req = w.draft(kind=Kind.ALLOCATION, requester=w.tech)
        w.act(req, w.tech, "submit")
        with self.assertRaisesMessage(WorkflowError, "not allowed"):
            w.act(req, w.tech, "issue_from_stock", asset_ids=[laptop.pk], campus=w.campus, building="A")
        req.refresh_from_db()
        self.assertNotIn(req, workflow.queue_for(w.tech))
        self.assertIn(req, workflow.queue_for(w.tech2))

    def test_technician_who_wrote_specs_cannot_confirm_them(self):
        w = self.w
        RoleGrant.objects.create(user=w.tech, role=Role.OPERATIONS_MANAGER)
        w.tech.clear_role_cache()
        req = w.draft()
        w.act(req, w.requester, "submit")
        w.act(req, w.pa, "pa_forward")
        w.act(req, w.director, "director_forward", comment="ok")
        w.act(req, w.csm, "csm_assign", technician=w.tech)
        w.act(req, w.tech, "submit_specifications", **w.specs(req))
        with self.assertRaisesMessage(WorkflowError, "not allowed"):
            w.act(req, w.tech, "ops_confirm")

    def test_requester_cannot_be_assigned_as_technician(self):
        w = self.w
        req = w.draft(requester=w.tech)
        w.act(req, w.tech, "submit")
        w.act(req, w.pa, "pa_forward")
        w.act(req, w.director, "director_forward", comment="ok")
        with self.assertRaisesMessage(WorkflowError, "own request"):
            w.act(req, w.csm, "csm_assign", technician=w.tech)

    def test_wrong_role_and_wrong_stage_are_refused(self):
        w = self.w
        req = w.draft()
        w.act(req, w.requester, "submit")
        with self.assertRaisesMessage(WorkflowError, "not allowed"):
            w.act(req, w.csm, "pa_forward")
        with self.assertRaisesMessage(WorkflowError, "not available"):
            w.act(req, w.director, "director_approve")
        with self.assertRaisesMessage(WorkflowError, "not available"):
            w.act(req, w.requester, "acknowledge")


class SignatureTests(TestCase):
    def setUp(self):
        self.w = World()
        w = self.w
        self.req = w.draft()
        w.act(self.req, w.requester, "submit")
        w.act(self.req, w.pa, "pa_forward")

    def test_wrong_password_is_refused_and_counted(self):
        w = self.w
        with self.assertRaises(SignatureError):
            w.act(self.req, w.director, "director_forward", comment="ok", password="wrong")
        self.req.refresh_from_db()
        self.assertEqual(self.req.state, State.DIRECTOR_REVIEW)
        self.assertTrue(
            LoginAttempt.objects.filter(identifier=w.director.email, successful=False, kind="signature").exists()
        )

    def test_repeated_bad_signatures_lock_the_account(self):
        w = self.w
        for _ in range(5):
            with self.assertRaises(SignatureError):
                w.act(self.req, w.director, "director_forward", comment="ok", password="wrong")
        with self.assertRaisesMessage(SignatureError, "Too many failed attempts"):
            w.act(self.req, w.director, "director_forward", comment="ok")

    @override_settings(ACQ_SIGNATURE_REQUIRES_PASSWORD=False)
    def test_signature_can_be_switched_off(self):
        w = self.w
        perform(self.req, w.director, "director_forward", data={"comment": "ok"})
        self.req.refresh_from_db()
        self.assertEqual(self.req.state, State.CSM_REVIEW)
        self.assertFalse(self.req.audit_entries.get(action="director_forward").signed)


class ConcurrencyAndRulesTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_stale_version_is_refused(self):
        w = self.w
        req = w.draft()
        w.act(req, w.requester, "submit")
        req.refresh_from_db()
        seen = req.version
        w.act(req, w.pa, "pa_forward")
        with self.assertRaises(StaleRequisition):
            perform(req, w.pa, "pa_return", data={"comment": "x"}, expected_version=seen)

    def test_comment_required_for_returns(self):
        w = self.w
        req = w.draft()
        w.act(req, w.requester, "submit")
        with self.assertRaisesMessage(WorkflowError, "comment is required"):
            w.act(req, w.pa, "pa_return")

    def test_specification_sheet_is_required(self):
        w = self.w
        req = w.draft()
        w.act(req, w.requester, "submit")
        w.act(req, w.pa, "pa_forward")
        w.act(req, w.director, "director_forward", comment="ok")
        w.act(req, w.csm, "csm_assign", technician=w.tech)
        with self.assertRaisesMessage(WorkflowError, "specification sheet"):
            w.act(req, w.tech, "submit_specifications", **w.specs(req, files=False))
        data = w.specs(req)
        data["specifications"] = {i.pk: {"specification": "", "cost": None} for i in req.items.all()}
        with self.assertRaisesMessage(WorkflowError, "recommended specification"):
            w.act(req, w.tech, "submit_specifications", **data)

    def test_requester_cannot_cancel_after_approval(self):
        w = self.w
        req = w.eqr_to_approval()
        w.act(req, w.director, "director_approve")
        with self.assertRaisesMessage(WorkflowError, "not available"):
            w.act(req, w.requester, "cancel", comment="Changed my mind")
        w.act(req, w.director, "management_cancel", comment="Budget withdrawn")
        req.refresh_from_db()
        self.assertEqual(req.state, State.CANCELLED)

    def test_requester_can_cancel_before_approval(self):
        w = self.w
        req = w.draft()
        w.act(req, w.requester, "submit")
        w.act(req, w.requester, "cancel", comment="No longer needed")
        req.refresh_from_db()
        self.assertEqual(req.state, State.CANCELLED)
        self.assertEqual(workflow.current_actors(req), [])

    def test_archived_requests_are_read_only(self):
        w = self.w
        req = w.draft()
        w.act(req, w.requester, "submit")
        req.refresh_from_db()
        req.archived_at = timezone.now()
        req.save()
        with self.assertRaisesMessage(WorkflowError, "not allowed"):
            w.act(req, w.pa, "pa_forward")

    def test_follow_up_date_must_be_in_future(self):
        w = self.w
        req = w.eqr_to_approval()
        w.act(req, w.director, "director_approve")
        w.act(req, w.tech, "send_to_procurement", procurement_reference="PR-5")
        with self.assertRaisesMessage(WorkflowError, "after today"):
            w.act(req, w.tech, "follow_up", comment="Called", next_follow_up_on=timezone.localdate())
        w.act(req, w.tech, "follow_up", comment="Called", next_follow_up_on=timezone.localdate() + timedelta(days=3))
        req.refresh_from_db()
        self.assertEqual(req.state, State.WITH_PROCUREMENT)

    def test_procurement_officer_posts_updates(self):
        w = self.w
        req = w.eqr_to_approval()
        w.act(req, w.director, "director_approve")
        w.act(req, w.tech, "send_to_procurement", procurement_reference="PR-5")
        self.assertIn(req, workflow.queue_for(w.procurement))
        w.act(
            req,
            w.procurement,
            "procurement_update",
            procurement_status="Purchase order raised",
            purchase_order="PO-9",
            comment="",
        )
        req.refresh_from_db()
        self.assertEqual(req.procurement_status, "Purchase order raised")
        self.assertTrue(w.tech.notifications.filter(title__contains="procurement update").exists())


class QueueTests(TestCase):
    def test_queue_lists_only_actionable_requests(self):
        w = World()
        req = w.draft()
        w.act(req, w.requester, "submit")
        self.assertEqual(workflow.queue_for(w.pa), [req])
        self.assertEqual(workflow.queue_for(w.director), [])
        self.assertEqual(workflow.queue_for(w.requester), [])
        w.act(req, w.pa, "pa_return", comment="More detail")
        req.refresh_from_db()
        self.assertEqual(workflow.queue_for(w.requester), [req])
        self.assertEqual(workflow.queue_for(w.pa), [])

    def test_route_map_marks_progress(self):
        w = World()
        req = w.draft()
        w.act(req, w.requester, "submit")
        w.act(req, w.pa, "pa_forward")
        req.refresh_from_db()
        statuses = [m["status"] for m in workflow.route_map(req)]
        self.assertEqual(statuses[:3], ["done", "done", "current"])
        self.assertTrue(all(s == "upcoming" for s in statuses[3:]))
