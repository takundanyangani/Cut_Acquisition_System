import csv
import tempfile
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.assets import services
from apps.assets.models import Asset, AssetMovement, AssetStatus, MovementKind
from apps.core.testing.factories import World


class AssetServiceTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_register_generates_tag_and_logs_movement(self):
        asset = self.w.stock(serial="abc-1 ")
        self.assertRegex(asset.asset_tag, r"^ICT-\d{4}-00001$")
        self.assertEqual(asset.serial_number, "ABC-1")
        self.assertEqual(asset.movements.get().kind, MovementKind.REGISTERED)

    def test_duplicate_serial_is_refused(self):
        self.w.stock(serial="DUP")
        with self.assertRaises(services.AssetError):
            self.w.stock(serial="dup")

    def test_manual_status_rules(self):
        asset = self.w.stock()
        with self.assertRaises(services.AssetError):
            services.change_status(asset, AssetStatus.ALLOCATED, performed_by=self.w.tech, note="x")
        with self.assertRaisesMessage(services.AssetError, "Only the Client Services Manager"):
            services.change_status(asset, AssetStatus.DISPOSED, performed_by=self.w.tech, note="Broken")
        with self.assertRaisesMessage(services.AssetError, "reason"):
            services.change_status(asset, AssetStatus.IN_REPAIR, performed_by=self.w.tech, note=" ")
        services.change_status(asset, AssetStatus.IN_REPAIR, performed_by=self.w.tech, note="Fan noise")
        asset.refresh_from_db()
        self.assertEqual(asset.status, AssetStatus.IN_REPAIR)
        services.change_status(asset, AssetStatus.DISPOSED, performed_by=self.w.csm, note="Board of survey")
        self.assertEqual(asset.movements.count(), 3)

    def test_movements_are_append_only(self):
        movement = self.w.stock().movements.get()
        movement.note = "edited"
        with self.assertRaises(ValidationError):
            movement.save()


class AssetViewTests(TestCase):
    def setUp(self):
        self.w = World()

    def test_register_requires_editor_role(self):
        self.client.force_login(self.w.requester)
        self.assertEqual(self.client.get(reverse("assets:list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("assets:create")).status_code, 403)
        self.client.force_login(self.w.ops)
        self.assertEqual(self.client.get(reverse("assets:list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("assets:create")).status_code, 403)

    def test_create_through_form(self):
        self.client.force_login(self.w.tech)
        response = self.client.post(
            reverse("assets:create"),
            {
                "category": self.w.laptop_cat.pk,
                "asset_tag": "",
                "serial_number": "NEW-77",
                "make": "HP",
                "model": "440",
                "specification": "i5",
                "condition": "new",
                "campus": self.w.campus.pk,
                "building": "Stores",
                "office": "",
                "supplier": "",
                "purchase_order_ref": "",
                "acquired_on": "",
                "warranty_expires_on": "",
                "notes": "",
            },
        )
        asset = Asset.objects.get(serial_number="NEW-77")
        self.assertRedirects(response, asset.get_absolute_url())

    def test_serial_required_for_laptops(self):
        self.client.force_login(self.w.tech)
        response = self.client.post(
            reverse("assets:create"),
            {
                "category": self.w.laptop_cat.pk,
                "make": "HP",
                "model": "440",
                "specification": "i5",
                "condition": "new",
                "building": "Stores",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "need a serial number")

    def test_holder_can_see_own_asset_only(self):
        asset = self.w.stock()
        self.client.force_login(self.w.requester)
        self.assertEqual(self.client.get(asset.get_absolute_url()).status_code, 404)


class VoucherRetentionTests(TestCase):
    def test_vouchers_stay_listed_for_a_year_after_their_request_is_archived(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.assets.models import IssueVoucher
        from apps.requisitions.models import Kind, Requisition

        w = World()
        monitor = w.stock(category=w.monitor_cat, serial="M-1")
        req = w.draft(kind=Kind.ALLOCATION, items=[(w.monitor_cat, "Monitor", 1)])
        w.act(req, w.requester, "submit")
        w.act(req, w.tech, "issue_from_stock", asset_ids=[monitor.pk], campus=w.campus, building="A")
        w.act(req, w.requester, "acknowledge")
        voucher = IssueVoucher.objects.get()
        Requisition.objects.filter(pk=req.pk).update(archived_at=timezone.now())
        self.client.force_login(w.tech)
        self.assertContains(self.client.get(reverse("assets:vouchers")), voucher.number)
        IssueVoucher.objects.filter(pk=voucher.pk).update(created_at=timezone.now() - timedelta(days=400))
        self.assertNotContains(self.client.get(reverse("assets:vouchers")), voucher.number)
        self.assertContains(self.client.get(reverse("assets:vouchers"), {"archived": "1"}), voucher.number)


class ImportTests(TestCase):
    def setUp(self):
        self.w = World()

    def _csv(self, rows, header):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
        handle.close()
        return handle.name

    def test_import_assets_dry_run_then_commit(self):
        path = self._csv(
            [
                ["CUT-0001", "LAP", "HP", "ProBook", "SER1", "i5", "good", "allocated", self.w.requester.email, "MAIN"],
                ["CUT-0002", "Monitor", "Dell", "P24", "SER2", "24in", "", "", "", ""],
            ],
            [
                "asset_tag",
                "category",
                "make",
                "model",
                "serial_number",
                "specification",
                "condition",
                "status",
                "holder_email",
                "campus",
            ],
        )
        call_command("import_assets", path, stdout=StringIO())
        self.assertFalse(Asset.objects.exists())
        call_command("import_assets", path, "--commit", stdout=StringIO())
        self.assertEqual(Asset.objects.count(), 2)
        held = Asset.objects.get(asset_tag="CUT-0001")
        self.assertEqual(held.holder, self.w.requester)
        self.assertEqual(held.status, AssetStatus.ALLOCATED)
        self.assertEqual(AssetMovement.objects.filter(kind=MovementKind.IMPORTED).count(), 2)

    def test_import_assets_reports_every_bad_row_and_saves_nothing(self):
        path = self._csv(
            [["CUT-0001", "LAP", "HP", "X", ""], ["CUT-0002", "NOPE", "HP", "X", "S"]],
            ["asset_tag", "category", "make", "model", "serial_number"],
        )
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("import_assets", path, "--commit", stdout=out)
        self.assertIn("line 2", out.getvalue())
        self.assertIn("line 3", out.getvalue())
        self.assertFalse(Asset.objects.exists())

    def test_import_users(self):
        path = self._csv(
            [
                ["new.person@test.cut.local", "New", "Person", "CUT9", "ENG", "technician;csm"],
                [self.w.requester.email, "Rita", "Renamed", "", "", ""],
            ],
            ["email", "first_name", "last_name", "staff_number", "department", "roles"],
        )
        call_command("import_users", path, "--commit", stdout=StringIO())
        person = User.objects.get(email="new.person@test.cut.local")
        self.assertEqual(set(person.direct_roles), {"technician", "csm"})
        self.assertTrue(person.has_usable_password())
        self.w.requester.refresh_from_db()
        self.assertEqual(self.w.requester.last_name, "Renamed")

    def test_import_users_rejects_unknown_role(self):
        path = self._csv([["a@test.cut.local", "A", "B", "boss"]], ["email", "first_name", "last_name", "roles"])
        with self.assertRaises(CommandError):
            call_command("import_users", path, "--commit", stdout=StringIO())
