"""End-to-end routes through CUT ICT 38, 40 and 42, driven through the engine."""

from django.core import mail
from django.test import TestCase

from apps.assets.models import Asset, AssetStatus, IssueVoucher, MovementKind
from apps.core.testing.factories import World
from apps.requisitions import workflow
from apps.requisitions.models import Kind, Requisition, State, verify_chain
from apps.requisitions.services import WorkflowError


class EquipmentRequestRouteTests(TestCase):
    """CUT ICT 38, then delivery and logging (ICT 42 §5.5, §5.6) and issue (ICT 40)."""

    def setUp(self):
        self.w = World()

    def test_full_procurement_route(self):
        w = self.w
        req = w.draft()
        self.assertIsNone(req.reference)

        w.act(req, w.requester, "submit")
        req.refresh_from_db()
        self.assertRegex(req.reference, r"^EQR-\d{4}-0001$")
        self.assertEqual(req.state, State.PA_REVIEW)
        self.assertIsNotNone(req.state_due_at)

        w.act(req, w.pa, "pa_forward")
        w.act(req, w.director, "director_forward", comment="Supported")
        w.act(req, w.csm, "csm_assign", technician=w.tech)
        req.refresh_from_db()
        self.assertEqual(req.state, State.TECHNICIAN_REVIEW)
        self.assertEqual(req.assigned_technician, w.tech)

        w.act(req, w.tech, "submit_specifications", **w.specs(req))
        req.refresh_from_db()
        self.assertEqual(req.state, State.OPS_REVIEW)
        self.assertEqual(req.items.first().recommended_specification, "Core i5, 16 GB, 512 GB SSD")
        self.assertTrue(req.attachments.filter(kind="spec_sheet").exists())

        w.act(req, w.ops, "ops_confirm")
        w.act(req, w.director, "director_approve")
        req.refresh_from_db()
        self.assertEqual(req.state, State.APPROVED)
        self.assertIsNotNone(req.approved_at)

        w.act(req, w.tech, "send_to_procurement", procurement_reference="PR-77")
        req.refresh_from_db()
        self.assertEqual(req.state, State.WITH_PROCUREMENT)
        self.assertEqual(req.procurement_reference, "PR-77")
        self.assertIsNotNone(req.next_follow_up_on)

        delivered = {
            "category": w.laptop_cat,
            "make": "Dell",
            "model": "Latitude",
            "serial_number": "DL-1",
            "asset_tag": "",
            "specification": "i5",
            "condition": "new",
            "warranty_expires_on": None,
            "supplier": "Acme",
            "purchase_order_ref": "PO-1",
            "acquired_on": None,
            "campus": w.campus,
        }
        w.act(req, w.tech, "record_delivery", assets=[delivered], delivery_complete=True)
        req.refresh_from_db()
        self.assertEqual(req.state, State.READY_FOR_ISSUE)
        laptop = req.delivered_assets.get()
        self.assertEqual(laptop.status, AssetStatus.IN_STOCK)
        self.assertTrue(laptop.movements.filter(kind=MovementKind.RECEIVED).exists())

        w.act(req, w.tech, "raise_voucher", asset_ids=[laptop.pk], campus=w.campus, building="Block A", office="A1")
        req.refresh_from_db()
        laptop.refresh_from_db()
        self.assertEqual(req.state, State.AWAITING_ACK)
        self.assertEqual(laptop.status, AssetStatus.RESERVED)
        voucher = req.vouchers.get()
        self.assertEqual(voucher.lines.get().serial_number, "DL-1")

        w.act(req, w.requester, "acknowledge")
        req.refresh_from_db()
        laptop.refresh_from_db()
        voucher.refresh_from_db()
        self.assertEqual(req.state, State.COMPLETED)
        self.assertIsNotNone(req.closed_at)
        self.assertEqual(laptop.status, AssetStatus.ALLOCATED)
        self.assertEqual(laptop.holder, w.requester)
        self.assertEqual(voucher.status, IssueVoucher.Status.ACKNOWLEDGED)
        self.assertIn("DL-1", voucher.acknowledgement_text)

        self.assertEqual(verify_chain(req), [])
        codes = list(req.audit_entries.values_list("action", flat=True))
        for code in [
            "submit",
            "pa_forward",
            "director_forward",
            "csm_assign",
            "submit_specifications",
            "ops_confirm",
            "director_approve",
            "send_to_procurement",
            "record_delivery",
            "raise_voucher",
            "acknowledge",
        ]:
            self.assertIn(code, codes)
        signed = set(req.audit_entries.filter(signed=True).values_list("action", flat=True))
        self.assertEqual(signed, {"director_forward", "ops_confirm", "director_approve", "acknowledge"})
        statement = req.audit_entries.get(action="director_approve").data["statement"]
        self.assertIn("approve this purchase request", statement)
        self.assertIn(req.reference, statement)

    def test_technician_can_issue_from_stock_instead_of_buying(self):
        w = self.w
        laptop = w.stock()
        req = w.draft()
        w.act(req, w.requester, "submit")
        w.act(req, w.pa, "pa_forward")
        w.act(req, w.director, "director_forward", comment="ok")
        w.act(req, w.csm, "csm_assign", technician=w.tech)
        w.act(req, w.tech, "issue_from_stock", asset_ids=[laptop.pk], campus=w.campus, building="B", office="")
        req.refresh_from_db()
        self.assertEqual(req.state, State.AWAITING_ACK)
        self.assertEqual(req.fulfilment_route, "stock")
        statuses = {m["status"] for m in workflow.route_map(req)}
        self.assertIn("skipped", statuses)

    def test_partial_delivery_stays_with_procurement(self):
        w = self.w
        req = w.eqr_to_approval()
        w.act(req, w.director, "director_approve")
        w.act(req, w.tech, "send_to_procurement", procurement_reference="PR-1")
        item = {
            "category": w.laptop_cat,
            "make": "HP",
            "model": "X",
            "serial_number": "P-1",
            "asset_tag": "",
            "specification": "s",
            "condition": "new",
            "warranty_expires_on": None,
            "supplier": "",
            "purchase_order_ref": "",
            "acquired_on": None,
            "campus": w.campus,
        }
        w.act(req, w.tech, "record_delivery", assets=[item], delivery_complete=False)
        req.refresh_from_db()
        self.assertEqual(req.state, State.WITH_PROCUREMENT)
        w.act(req, w.tech, "record_delivery", assets=[], delivery_complete=True)
        req.refresh_from_db()
        self.assertEqual(req.state, State.READY_FOR_ISSUE)

    def test_delivery_cannot_complete_with_nothing_logged(self):
        w = self.w
        req = w.eqr_to_approval()
        w.act(req, w.director, "director_approve")
        w.act(req, w.tech, "send_to_procurement", procurement_reference="PR-1")
        with self.assertRaisesMessage(WorkflowError, "Log the delivered items"):
            w.act(req, w.tech, "record_delivery", assets=[], delivery_complete=True)

    def test_notifications_reach_next_desk_and_requester(self):
        w = self.w
        req = w.draft()
        with self.captureOnCommitCallbacks(execute=True):
            w.act(req, w.requester, "submit")
        self.assertTrue(w.pa.notifications.filter(title__contains="needs your action").exists())
        self.assertTrue(any(w.pa.email in m.to for m in mail.outbox))
        w.act(req, w.pa, "pa_forward")
        self.assertTrue(w.director.notifications.exists())
        self.assertTrue(w.requester.notifications.filter(title__contains="ICT Director review").exists())


class ReplacementRouteTests(TestCase):
    """CUT ICT 42."""

    def setUp(self):
        self.w = World()

    def _old_machine(self):
        w = self.w
        old = w.stock(serial="OLD-1")
        alloc = w.draft(kind=Kind.ALLOCATION)
        w.act(alloc, w.requester, "submit")
        w.act(alloc, w.tech, "issue_from_stock", asset_ids=[old.pk], campus=w.campus, building="A", office="")
        w.act(alloc, w.requester, "acknowledge")
        old.refresh_from_db()
        self.assertEqual(old.holder, w.requester)
        return old

    def test_replacement_route_returns_old_machine(self):
        w = self.w
        old = self._old_machine()
        req = w.draft(kind=Kind.REPLACEMENT, asset_to_replace=old, replacement_reason="faulty")
        w.act(req, w.requester, "submit")
        req.refresh_from_db()
        self.assertEqual(req.state, State.CSM_REVIEW)
        self.assertRegex(req.reference, r"^RPL-")

        w.act(req, w.csm, "csm_recommend", technician=w.tech)
        req.refresh_from_db()
        self.assertEqual(req.state, State.DIRECTOR_APPROVAL)
        self.assertTrue(req.csm_recommends)

        w.act(req, w.director, "director_approve_replacement")
        req.refresh_from_db()
        self.assertEqual(req.state, State.TECHNICIAN_REVIEW)

        data = w.specs(req)
        data["procurement_reference"] = "PR-9"
        w.act(req, w.tech, "initiate_procurement", **data)
        req.refresh_from_db()
        self.assertEqual(req.state, State.WITH_PROCUREMENT)

        item = {
            "category": w.laptop_cat,
            "make": "Dell",
            "model": "New",
            "serial_number": "NEW-1",
            "asset_tag": "",
            "specification": "s",
            "condition": "new",
            "warranty_expires_on": None,
            "supplier": "",
            "purchase_order_ref": "",
            "acquired_on": None,
            "campus": w.campus,
        }
        w.act(req, w.tech, "record_delivery", assets=[item], delivery_complete=True)
        new = req.delivered_assets.get()
        with self.assertRaisesMessage(WorkflowError, "old machine"):
            w.act(req, w.tech, "raise_voucher", asset_ids=[new.pk], campus=w.campus, building="A")
        w.act(
            req,
            w.tech,
            "raise_voucher",
            asset_ids=[new.pk],
            campus=w.campus,
            building="A",
            replaced_collected=True,
            replaced_condition="faulty",
        )
        old.refresh_from_db()
        self.assertEqual(old.status, AssetStatus.RETURNED)
        self.assertIsNone(old.holder)
        self.assertEqual(old.condition, "faulty")
        w.act(req, w.requester, "acknowledge")
        new.refresh_from_db()
        self.assertEqual(new.holder, w.requester)

    def test_csm_not_recommending_still_reaches_director(self):
        w = self.w
        req = w.draft(
            kind=Kind.REPLACEMENT, replaced_item_description="Old Dell, serial X1", replacement_reason="obsolete"
        )
        w.act(req, w.requester, "submit")
        with self.assertRaisesMessage(WorkflowError, "comment is required"):
            w.act(req, w.csm, "csm_not_recommend", technician=w.tech)
        w.act(req, w.csm, "csm_not_recommend", technician=w.tech, comment="Still serviceable")
        req.refresh_from_db()
        self.assertEqual(req.state, State.DIRECTOR_APPROVAL)
        self.assertFalse(req.csm_recommends)
        w.act(req, w.director, "director_decline", comment="Agree with the CSM")
        req.refresh_from_db()
        self.assertEqual(req.state, State.DECLINED)

    def test_replacement_needs_a_machine_and_reason(self):
        w = self.w
        req = w.draft(kind=Kind.REPLACEMENT)
        with self.assertRaisesMessage(WorkflowError, "which machine"):
            w.act(req, w.requester, "submit")


class AllocationRouteTests(TestCase):
    """CUT ICT 40, and conversion to CUT ICT 38 when there is no stock."""

    def setUp(self):
        self.w = World()

    def test_unassigned_queue_visible_to_all_technicians(self):
        w = self.w
        req = w.draft(kind=Kind.ALLOCATION)
        w.act(req, w.requester, "submit")
        req.refresh_from_db()
        self.assertEqual(req.state, State.TECHNICIAN_REVIEW)
        self.assertIn(req, workflow.queue_for(w.tech))
        self.assertIn(req, workflow.queue_for(w.tech2))
        w.act(req, w.tech2, "claim")
        req.refresh_from_db()
        self.assertEqual(req.assigned_technician, w.tech2)
        self.assertNotIn(req, workflow.queue_for(w.tech))

    def test_dispute_returns_assets_to_stock(self):
        w = self.w
        monitor = w.stock(category=w.monitor_cat, serial="M-1")
        req = w.draft(kind=Kind.ALLOCATION, items=[(w.monitor_cat, "Monitor", 1)])
        w.act(req, w.requester, "submit")
        w.act(req, w.tech, "issue_from_stock", asset_ids=[monitor.pk], campus=w.campus, building="A")
        w.act(req, w.requester, "dispute", comment="Serial on the box is M-2")
        req.refresh_from_db()
        monitor.refresh_from_db()
        self.assertEqual(req.state, State.READY_FOR_ISSUE)
        self.assertEqual(monitor.status, AssetStatus.IN_STOCK)
        self.assertEqual(req.vouchers.get().status, IssueVoucher.Status.DISPUTED)
        w.act(req, w.tech, "raise_voucher", asset_ids=[monitor.pk], campus=w.campus, building="A")
        self.assertEqual(req.vouchers.count(), 2)

    def test_short_voucher_needs_an_explanation(self):
        w = self.w
        monitor = w.stock(category=w.monitor_cat, serial="M-7")
        req = w.draft(kind=Kind.ALLOCATION, items=[(w.monitor_cat, "Monitor", 2)])
        w.act(req, w.requester, "submit")
        with self.assertRaisesMessage(WorkflowError, "lists 2 items"):
            w.act(req, w.tech, "issue_from_stock", asset_ids=[monitor.pk], campus=w.campus, building="A")
        w.act(
            req,
            w.tech,
            "issue_from_stock",
            asset_ids=[monitor.pk],
            campus=w.campus,
            building="A",
            comment="Second monitor follows next week",
        )
        req.refresh_from_db()
        self.assertEqual(req.state, State.AWAITING_ACK)

    def test_withdraw_voucher(self):
        w = self.w
        monitor = w.stock(category=w.monitor_cat, serial="M-1")
        req = w.draft(kind=Kind.ALLOCATION)
        w.act(req, w.requester, "submit")
        w.act(req, w.tech, "issue_from_stock", asset_ids=[monitor.pk], campus=w.campus, building="A")
        w.act(req, w.tech, "withdraw_voucher", comment="Wrong unit")
        monitor.refresh_from_db()
        self.assertEqual(monitor.status, AssetStatus.IN_STOCK)

    def test_convert_to_equipment_request(self):
        w = self.w
        req = w.draft(kind=Kind.ALLOCATION, items=[(w.laptop_cat, "Laptop", 2)])
        w.act(req, w.requester, "submit")
        w.act(req, w.tech, "convert", comment="No laptops in stock")
        req.refresh_from_db()
        self.assertEqual(req.state, State.CONVERTED)
        eqr = req.converted_requests.get()
        self.assertEqual(eqr.kind, Kind.EQUIPMENT)
        self.assertEqual(eqr.state, State.PA_REVIEW)
        self.assertEqual(eqr.requester, w.requester)
        self.assertEqual(eqr.items.get().quantity, 2)
        self.assertEqual(verify_chain(eqr), [])
        self.assertTrue(w.pa.notifications.filter(title__contains=eqr.reference).exists())
        self.assertTrue(w.requester.notifications.filter(title__contains=eqr.reference).exists())

    def test_same_asset_cannot_be_issued_twice(self):
        w = self.w
        monitor = w.stock(category=w.monitor_cat, serial="M-1")
        first = w.draft(kind=Kind.ALLOCATION)
        second = w.draft(kind=Kind.ALLOCATION, requester=w.other)
        for req, person in ((first, w.requester), (second, w.other)):
            w.act(req, person, "submit")
        w.act(first, w.tech, "issue_from_stock", asset_ids=[monitor.pk], campus=w.campus, building="A")
        with self.assertRaisesMessage(WorkflowError, "no longer in stock"):
            w.act(second, w.tech, "issue_from_stock", asset_ids=[monitor.pk], campus=w.campus, building="A")
        self.assertEqual(Asset.objects.get(pk=monitor.pk).status, AssetStatus.RESERVED)


class ReturnAndResubmitTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_resubmission_goes_back_to_the_reviewer_who_returned_it(self):
        w = self.w
        req = w.draft()
        w.act(req, w.requester, "submit")
        w.act(req, w.pa, "pa_forward")
        w.act(req, w.director, "director_return", comment="Which course?")
        req.refresh_from_db()
        self.assertEqual(req.state, State.RETURNED)
        self.assertEqual(req.returned_from_state, State.DIRECTOR_REVIEW)
        reference = req.reference
        w.act(req, w.requester, "submit", comment="Added the course code")
        req.refresh_from_db()
        self.assertEqual(req.state, State.DIRECTOR_REVIEW)
        self.assertEqual(req.reference, reference)
        self.assertEqual(req.returned_from_state, "")

    def test_ops_return_sends_back_to_technician(self):
        w = self.w
        req = w.draft()
        w.act(req, w.requester, "submit")
        w.act(req, w.pa, "pa_forward")
        w.act(req, w.director, "director_forward", comment="ok")
        w.act(req, w.csm, "csm_assign", technician=w.tech)
        w.act(req, w.tech, "submit_specifications", **w.specs(req))
        w.act(req, w.ops, "ops_return", comment="Need a 3-year warranty")
        req.refresh_from_db()
        self.assertEqual(req.state, State.TECHNICIAN_REVIEW)


class ReferenceNumberTests(TestCase):
    def test_numbers_are_sequential_per_type(self):
        w = World()
        refs = []
        for kind in (Kind.EQUIPMENT, Kind.EQUIPMENT, Kind.ALLOCATION):
            req = w.draft(kind=kind)
            w.act(req, w.requester, "submit")
            req.refresh_from_db()
            refs.append(req.reference)
        self.assertTrue(refs[0].endswith("-0001"))
        self.assertTrue(refs[1].endswith("-0002"))
        self.assertTrue(refs[2].startswith("ALC-") and refs[2].endswith("-0001"))

    def test_drafts_do_not_take_numbers(self):
        w = World()
        w.draft()
        w.draft()
        req = w.draft()
        w.act(req, w.requester, "submit")
        req.refresh_from_db()
        self.assertTrue(req.reference.endswith("-0001"))
        self.assertEqual(Requisition.objects.filter(reference__isnull=True).count(), 2)
