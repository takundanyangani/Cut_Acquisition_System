from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.core.testing.factories import PASSWORD, World, pdf
from apps.requisitions.models import Attachment, Kind, Requisition, State


def item_post(prefix="items", rows=()):
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": "0",
        f"{prefix}-MIN_NUM_FORMS": "1",
        f"{prefix}-MAX_NUM_FORMS": "30",
    }
    for i, (category, description, quantity) in enumerate(rows):
        data.update(
            {
                f"{prefix}-{i}-category": str(category.pk),
                f"{prefix}-{i}-description": description,
                f"{prefix}-{i}-quantity": str(quantity),
                f"{prefix}-{i}-requester_notes": "",
            }
        )
    return data


class RequestFormTests(TestCase):
    def setUp(self):
        self.w = World()
        self.client.force_login(self.w.requester)

    def _post(self, kind="equipment", submit=True, **overrides):
        data = {
            "title": "Laptop for teaching",
            "justification": "Lectures in CAD",
            "priority": "routine",
            "campus": str(self.w.campus.pk),
            "building": "Block A",
            "office": "A1",
        }
        data.update(item_post(rows=[(self.w.laptop_cat, "Laptop", 1)]))
        data.update(overrides)
        if submit:
            data["submit"] = "1"
        else:
            data["save"] = "1"
        return self.client.post(reverse("requisitions:create", args=[kind]), data)

    def test_create_and_submit(self):
        response = self._post()
        req = Requisition.objects.get()
        self.assertRedirects(response, req.get_absolute_url())
        self.assertEqual(req.state, State.PA_REVIEW)
        self.assertEqual(req.items.count(), 1)

    def test_save_draft_then_discard(self):
        self._post(submit=False)
        req = Requisition.objects.get()
        self.assertEqual(req.state, State.DRAFT)
        self.assertIsNone(req.reference)
        response = self.client.post(reverse("requisitions:discard", args=[req.public_id]))
        self.assertRedirects(response, reverse("requisitions:mine"))
        self.assertFalse(Requisition.objects.exists())

    def test_items_are_required(self):
        data = item_post(rows=[])
        response = self._post(**data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Requisition.objects.exists())

    def test_form_starts_with_profile_location(self):
        response = self.client.get(reverse("requisitions:create", args=["allocation"]))
        self.assertContains(response, f'<option value="{self.w.campus.pk}" selected>')

    def test_upload_rejects_disguised_files(self):
        fake = SimpleUploadedFile("spec.pdf", b"MZ\x90\x00 not a pdf", content_type="application/pdf")
        response = self._post(attachments=fake)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "does not look like a real PDF")
        self.assertFalse(Requisition.objects.exists())

    def test_upload_rejects_wrong_extension(self):
        exe = SimpleUploadedFile("tool.exe", b"MZ", content_type="application/octet-stream")
        response = self._post(attachments=exe)
        self.assertContains(response, "only PDF, Word, Excel, PNG or JPEG")

    def test_edit_returned_request_logs_change_and_resubmits(self):
        w = self.w
        self._post()
        req = Requisition.objects.get()
        w.act(req, w.pa, "pa_return", comment="Add the room")
        req.refresh_from_db()
        data = {
            "title": "Laptop for CAD teaching",
            "justification": "Lectures in CAD",
            "priority": "routine",
            "campus": str(w.campus.pk),
            "building": "Block A",
            "office": "A12",
            "submit": "1",
            "comment": "Added room",
        }
        item = req.items.get()
        data.update(
            {
                "items-TOTAL_FORMS": "1",
                "items-INITIAL_FORMS": "1",
                "items-MIN_NUM_FORMS": "1",
                "items-MAX_NUM_FORMS": "30",
                "items-0-id": str(item.pk),
                "items-0-requisition": str(req.pk),
                "items-0-category": str(w.laptop_cat.pk),
                "items-0-description": "Laptop",
                "items-0-quantity": "1",
                "items-0-requester_notes": "",
            }
        )
        response = self.client.post(reverse("requisitions:edit", args=[req.public_id]), data)
        self.assertRedirects(response, req.get_absolute_url())
        req.refresh_from_db()
        self.assertEqual(req.state, State.PA_REVIEW)
        self.assertEqual(req.office, "A12")
        edit = req.audit_entries.get(action="edited")
        self.assertIn("office", edit.data["fields"])

    def test_cannot_edit_after_submission(self):
        self._post()
        req = Requisition.objects.get()
        response = self.client.get(reverse("requisitions:edit", args=[req.public_id]))
        self.assertRedirects(response, req.get_absolute_url())


class AccessTests(TestCase):
    def setUp(self):
        self.w = World()
        self.req = self.w.draft()
        self.w.act(self.req, self.w.requester, "submit")
        self.req.refresh_from_db()

    def test_other_staff_get_404_not_403(self):
        self.client.force_login(self.w.other)
        response = self.client.get(self.req.get_absolute_url())
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Not on file", status_code=404)

    def test_ict_staff_and_auditor_can_read(self):
        for user in (self.w.tech, self.w.auditor, self.w.director):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.req.get_absolute_url()).status_code, 200)

    def test_drafts_are_private(self):
        draft = self.w.draft()
        self.client.force_login(self.w.director)
        self.assertEqual(self.client.get(draft.get_absolute_url()).status_code, 404)

    def test_procurement_sees_only_requests_sent_to_it(self):
        self.client.force_login(self.w.procurement)
        self.assertEqual(self.client.get(self.req.get_absolute_url()).status_code, 404)

    def test_signed_out_users_go_to_sign_in(self):
        response = self.client.get(self.req.get_absolute_url())
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_attachment_download_is_permission_checked(self):
        from apps.requisitions.services import save_attachment

        attachment = save_attachment(self.req, self.w.requester, pdf("quote.pdf"), Attachment.Kind.QUOTATION)
        url = reverse("requisitions:attachment", args=[attachment.public_id])
        self.client.force_login(self.w.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.w.requester)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content)[:5], b"%PDF-")

    def test_export_needs_report_role(self):
        self.client.force_login(self.w.requester)
        self.assertEqual(self.client.get(reverse("requisitions:export")).status_code, 403)
        self.client.force_login(self.w.director)
        response = self.client.get(reverse("requisitions:export"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        from io import BytesIO

        from openpyxl import load_workbook

        sheet = load_workbook(BytesIO(response.content)).active
        self.assertEqual(sheet["A1"].value, "Reference")
        self.assertEqual(sheet["A2"].value, self.req.reference)


class ActionPageTests(TestCase):
    def setUp(self):
        self.w = World()
        self.req = self.w.draft()
        self.w.act(self.req, self.w.requester, "submit")
        self.w.act(self.req, self.w.pa, "pa_forward")
        self.req.refresh_from_db()
        self.url = reverse("requisitions:action", args=[self.req.public_id, "director_forward"])

    def test_action_page_shows_signature_statement(self):
        self.client.force_login(self.w.director)
        response = self.client.get(self.url)
        self.assertContains(response, "signing as ICT Director")
        self.assertContains(response, 'name="password"')

    def test_post_with_wrong_password_shows_error(self):
        self.client.force_login(self.w.director)
        response = self.client.post(self.url, {"version": self.req.version, "comment": "ok", "password": "nope"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "password is not correct")
        self.req.refresh_from_db()
        self.assertEqual(self.req.state, State.DIRECTOR_REVIEW)

    def test_post_signs_and_moves_on(self):
        self.client.force_login(self.w.director)
        response = self.client.post(self.url, {"version": self.req.version, "comment": "ok", "password": PASSWORD})
        self.assertRedirects(response, self.req.get_absolute_url())
        self.req.refresh_from_db()
        self.assertEqual(self.req.state, State.CSM_REVIEW)

    def test_stale_form_is_refused_politely(self):
        self.client.force_login(self.w.director)
        response = self.client.post(
            self.url, {"version": self.req.version - 1, "comment": "ok", "password": PASSWORD}, follow=True
        )
        self.assertContains(response, "Someone else updated this request")

    def test_other_roles_get_403(self):
        self.client.force_login(self.w.csm)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_assign_page_lists_technicians_only(self):
        w = self.w
        w.act(self.req, w.director, "director_forward", comment="ok")
        self.client.force_login(w.csm)
        response = self.client.get(reverse("requisitions:action", args=[self.req.public_id, "csm_assign"]))
        self.assertContains(response, w.tech.display_name)
        self.assertNotContains(response, f">{w.ops.display_name}<")

    def test_voucher_and_acknowledge_pages_through_views(self):
        w = self.w
        monitor = w.stock(category=w.monitor_cat, serial="MON-9")
        alloc = w.draft(kind=Kind.ALLOCATION, items=[(w.monitor_cat, "Monitor", 1)])
        w.act(alloc, w.requester, "submit")
        alloc.refresh_from_db()
        self.client.force_login(w.tech)
        url = reverse("requisitions:action", args=[alloc.public_id, "issue_from_stock"])
        page = self.client.get(url)
        self.assertContains(page, "MON-9")
        response = self.client.post(
            url,
            {
                "version": alloc.version,
                "assets": [monitor.pk],
                "campus": w.campus.pk,
                "building": "Block A",
                "office": "A1",
                "notes": "",
                "comment": "",
            },
        )
        self.assertRedirects(response, alloc.get_absolute_url())
        alloc.refresh_from_db()
        self.assertEqual(alloc.state, State.AWAITING_ACK)

        self.client.force_login(w.requester)
        url = reverse("requisitions:action", args=[alloc.public_id, "acknowledge"])
        self.assertContains(self.client.get(url), "MON-9")
        missing = self.client.post(url, {"version": alloc.version, "password": PASSWORD})
        self.assertEqual(missing.status_code, 200)
        alloc.refresh_from_db()
        self.assertEqual(alloc.state, State.AWAITING_ACK)
        response = self.client.post(
            url,
            {
                "version": alloc.version,
                "password": PASSWORD,
                "confirm_serials": "on",
                "confirm_specification": "on",
                "confirm_condition": "on",
            },
        )
        self.assertRedirects(response, alloc.get_absolute_url())
        alloc.refresh_from_db()
        self.assertEqual(alloc.state, State.COMPLETED)

    def test_delivery_page_creates_assets(self):
        w = self.w
        req = w.eqr_to_approval()
        w.act(req, w.director, "director_approve")
        w.act(req, w.tech, "send_to_procurement", procurement_reference="PR-1")
        req.refresh_from_db()
        self.client.force_login(w.tech)
        url = reverse("requisitions:action", args=[req.public_id, "record_delivery"])
        self.assertEqual(self.client.get(url).status_code, 200)
        data = {
            "version": req.version,
            "comment": "",
            "supplier": "Acme",
            "purchase_order_ref": "PO-3",
            "delivery_complete": "on",
            "asset-TOTAL_FORMS": "2",
            "asset-INITIAL_FORMS": "0",
            "asset-MIN_NUM_FORMS": "0",
            "asset-MAX_NUM_FORMS": "50",
            "asset-0-category": w.laptop_cat.pk,
            "asset-0-make": "Dell",
            "asset-0-model": "5450",
            "asset-0-serial_number": "D-55",
            "asset-0-asset_tag": "",
            "asset-0-specification": "i7",
            "asset-0-condition": "new",
            "asset-0-warranty_expires_on": "",
            "asset-1-category": "",
            "asset-1-make": "",
            "asset-1-model": "",
            "asset-1-serial_number": "",
            "asset-1-asset_tag": "",
            "asset-1-specification": "",
            "asset-1-condition": "new",
            "asset-1-warranty_expires_on": "",
        }
        response = self.client.post(url, data)
        self.assertRedirects(response, req.get_absolute_url())
        req.refresh_from_db()
        self.assertEqual(req.state, State.READY_FOR_ISSUE)
        asset = req.delivered_assets.get()
        self.assertEqual(asset.serial_number, "D-55")
        self.assertEqual(asset.supplier, "Acme")


class PagesTests(TestCase):
    def test_main_pages_render_for_each_role(self):
        w = World()
        req = w.eqr_to_approval()
        pages = [
            reverse("core:dashboard"),
            reverse("requisitions:mine"),
            reverse("requisitions:queue"),
            reverse("requisitions:choose_kind"),
            reverse("core:process"),
            reverse("core:notifications"),
            req.get_absolute_url(),
            reverse("requisitions:print", args=[req.public_id]),
        ]
        for user in (w.director, w.tech, w.ops, w.auditor):
            self.client.force_login(user)
            for url in pages:
                with self.subTest(user=user.email, url=url):
                    self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(w.director)
        self.assertEqual(self.client.get(reverse("core:reports")).status_code, 200)
        self.assertEqual(self.client.get(reverse("requisitions:all")).status_code, 200)
        self.client.force_login(w.requester)
        self.assertEqual(self.client.get(reverse("core:reports")).status_code, 403)

    def test_search_jumps_to_exact_reference(self):
        w = World()
        req = w.draft()
        w.act(req, w.requester, "submit")
        req.refresh_from_db()
        self.client.force_login(w.requester)
        response = self.client.get(reverse("requisitions:search"), {"q": req.reference.lower()})
        self.assertRedirects(response, req.get_absolute_url())

    def test_notes(self):
        w = World()
        req = w.draft()
        w.act(req, w.requester, "submit")
        req.refresh_from_db()
        self.client.force_login(w.pa)
        self.client.post(reverse("requisitions:add_note", args=[req.public_id]), {"note": "Checking budget"})
        self.assertTrue(req.audit_entries.filter(action="note", comment="Checking budget").exists())
        self.assertTrue(w.requester.notifications.filter(title__contains="new note").exists())
        self.client.force_login(w.other)
        response = self.client.post(reverse("requisitions:add_note", args=[req.public_id]), {"note": "Hi"})
        self.assertEqual(response.status_code, 404)
