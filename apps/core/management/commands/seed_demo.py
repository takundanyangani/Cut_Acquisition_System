"""Load demonstration data: campuses, departments, holidays, one account per
role, stock, and requests at different stages.

Every request is driven through the real workflow engine, so routing slips,
signatures, vouchers and hash chains are genuine. The clock is moved back
while seeding so the reports show realistic stage times.

The names are fictional. Do not run this on the production database.
"""

from contextlib import contextmanager
from datetime import date, timedelta
from unittest import mock

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, RoleGrant, User
from apps.assets import services as asset_services
from apps.assets.models import AssetCategory, AssetStatus, Condition
from apps.core.models import Campus, Department, Holiday
from apps.requisitions import services
from apps.requisitions.models import Kind, Priority, ReplacementReason, Requisition, RequisitionItem

DOMAIN = "demo.cut.local"


def easter(year):
    """Gregorian Easter Sunday (the anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    weekday_offset = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * weekday_offset) // 451
    month, day = divmod(h + weekday_offset - 7 * m + 114, 31)
    return date(year, month, day + 1)


def zimbabwe_holidays(year):
    """Fixed and Easter-based public holidays. Verify against the Government
    Gazette each year; moved holidays are not modelled."""
    sunday = easter(year)
    first_aug = date(year, 8, 1)
    first_monday = first_aug + timedelta(days=(7 - first_aug.weekday()) % 7)
    heroes = first_monday + timedelta(days=7)
    return [
        (date(year, 1, 1), "New Year's Day"),
        (date(year, 2, 21), "Robert Gabriel Mugabe National Youth Day"),
        (sunday - timedelta(days=2), "Good Friday"),
        (sunday - timedelta(days=1), "Holy Saturday"),
        (sunday + timedelta(days=1), "Easter Monday"),
        (date(year, 4, 18), "Independence Day"),
        (date(year, 5, 1), "Workers' Day"),
        (date(year, 5, 25), "Africa Day"),
        (heroes, "Heroes' Day"),
        (heroes + timedelta(days=1), "Defence Forces Day"),
        (date(year, 12, 22), "Unity Day"),
        (date(year, 12, 25), "Christmas Day"),
        (date(year, 12, 26), "Boxing Day"),
    ]


class Clock:
    """Moves Django's idea of 'now' forward step by step while seeding."""

    def __init__(self, start):
        self.current = start

    def advance(self, hours=0, days=0):
        """Move forward, keeping inside 08:00 to 17:00 on weekdays so stage times look real."""
        self.current += timedelta(hours=hours, days=days)
        while True:
            if self.current.hour >= 17:
                overflow = self.current - self.current.replace(hour=17, minute=0)
                self.current = (self.current + timedelta(days=1)).replace(hour=8, minute=0) + overflow
            elif self.current.hour < 8:
                self.current = self.current.replace(hour=8, minute=30)
            elif self.current.weekday() >= 5:
                self.current += timedelta(days=1)
            else:
                return self.current

    def jump_to(self, moment):
        """Move forward to `moment` (never backwards), then into working hours."""
        if moment > self.current:
            self.current = moment
        return self.advance()

    def now(self):
        return self.current


@contextmanager
def frozen(clock):
    with mock.patch("django.utils.timezone.now", side_effect=clock.now):
        yield


class Command(BaseCommand):
    help = "Load fictional demonstration data. Refuses to run when DEBUG is off unless --allow-production."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="Demo-Pass-2026", help="Password for every demo account.")
        parser.add_argument("--allow-production", action="store_true")

    def handle(self, *args, password, allow_production=False, **options):
        if not settings.DEBUG and not allow_production:
            raise CommandError("DEBUG is off. Demo data belongs on a test server; pass --allow-production to insist.")
        if User.objects.filter(email__endswith=f"@{DOMAIN}").exists():
            raise CommandError("Demo data is already loaded.")
        self.password = password
        with transaction.atomic():
            self._reference_data()
            self._people()
            self._stock()
            self._requests()
        self.stdout.write(self.style.SUCCESS("Demo data loaded. Accounts (all with the same password):"))
        for user in User.objects.filter(email__endswith=f"@{DOMAIN}").order_by("email"):
            roles = ", ".join(user.role_labels()) or "Requester"
            self.stdout.write(f"  {user.email:<36} {roles}")
        self.stdout.write(f"Password: {password}")

    # -- reference data -------------------------------------------------------

    def _reference_data(self):
        self.main, _ = Campus.objects.get_or_create(code="MAIN", defaults={"name": "Main Campus"})
        self.second, _ = Campus.objects.get_or_create(code="SAT", defaults={"name": "Satellite Campus"})
        departments = [
            ("ICT", "ICT Services"),
            ("ENG", "Engineering"),
            ("LIB", "Library"),
            ("FIN", "Finance"),
            ("REG", "Registry"),
            ("PRC", "Procurement"),
        ]
        self.departments = {
            code: Department.objects.get_or_create(code=code, defaults={"name": name})[0] for code, name in departments
        }
        this_year = timezone.localdate().year
        for year in (this_year, this_year + 1):
            for day, name in zimbabwe_holidays(year):
                Holiday.objects.get_or_create(date=day, defaults={"name": name})

    def _user(self, local, first, last, *, title="", job="", dept="ENG", roles=(), superuser=False, staff_no=None):
        user = User.objects.create_user(
            email=f"{local}@{DOMAIN}",
            password=self.password,
            first_name=first,
            last_name=last,
            title=title,
            job_title=job,
            department=self.departments[dept],
            campus=self.main,
            building={"ENG": "Engineering Block", "LIB": "Library Building"}.get(dept, "Administration Block"),
            office=f"Room {10 + User.objects.count()}",
            staff_number=staff_no,
            is_staff=superuser,
            is_superuser=superuser,
        )
        for role in roles:
            RoleGrant.objects.create(user=user, role=role)
        return user

    def _people(self):
        self.tendai = self._user("tendai.moyo", "Tendai", "Moyo", job="Lecturer", staff_no="CUT1001")
        self.memory = self._user(
            "memory.banda", "Memory", "Banda", job="Senior Librarian", dept="LIB", staff_no="CUT1002"
        )
        self.farai = self._user(
            "farai.ncube",
            "Farai",
            "Ncube",
            job="Personal Advisor to the ICT Director",
            dept="ICT",
            roles=[Role.PERSONAL_ADVISOR],
            staff_no="CUT2001",
        )
        self.director = self._user(
            "rumbidzai.mutasa",
            "Rumbidzai",
            "Mutasa",
            title="Dr",
            job="ICT Director",
            dept="ICT",
            roles=[Role.ICT_DIRECTOR],
            staff_no="CUT2002",
        )
        self.csm = self._user(
            "tatenda.sibanda",
            "Tatenda",
            "Sibanda",
            job="Client Services Manager",
            dept="ICT",
            roles=[Role.CLIENT_SERVICES_MANAGER],
            staff_no="CUT2003",
        )
        self.tech = self._user(
            "kuda.dube",
            "Kudakwashe",
            "Dube",
            job="Help Desk Technician",
            dept="ICT",
            roles=[Role.TECHNICIAN],
            staff_no="CUT2004",
        )
        self.tech2 = self._user(
            "nyasha.gumbo",
            "Nyasha",
            "Gumbo",
            job="Help Desk Technician",
            dept="ICT",
            roles=[Role.TECHNICIAN],
            staff_no="CUT2005",
        )
        self.ops = self._user(
            "chiedza.marufu",
            "Chiedza",
            "Marufu",
            job="Operations Manager",
            dept="ICT",
            roles=[Role.OPERATIONS_MANAGER],
            staff_no="CUT2006",
        )
        self.procurement = self._user(
            "blessing.mhlanga",
            "Blessing",
            "Mhlanga",
            job="Procurement Officer",
            dept="PRC",
            roles=[Role.PROCUREMENT_OFFICER],
            staff_no="CUT3001",
        )
        self.auditor = self._user(
            "ruvimbo.zhou",
            "Ruvimbo",
            "Zhou",
            job="Quality Assurance Officer",
            dept="REG",
            roles=[Role.AUDITOR],
            staff_no="CUT3002",
        )
        self.admin = self._user(
            "admin", "System", "Administrator", dept="ICT", roles=[Role.SYSTEM_ADMIN], superuser=True
        )

    # -- stock -------------------------------------------------------------------

    def _asset(self, code, make, model, serial, spec, **extra):
        return asset_services.register_asset(
            performed_by=self.tech,
            category=AssetCategory.objects.get(code=code),
            make=make,
            model=model,
            serial_number=serial,
            specification=spec,
            condition=extra.pop("condition", Condition.NEW),
            campus=self.main,
            building="ICT Stores",
            office="Store 1",
            supplier=extra.pop("supplier", "Demo Supplies (Pvt) Ltd"),
            acquired_on=extra.pop("acquired_on", timezone.localdate() - timedelta(days=40)),
            note="Demo stock",
            **extra,
        )

    def _stock(self):
        self._asset("MON", "Dell", "P2423D 24-inch", "CN0P2423D01", "24-inch QHD IPS, HDMI and DisplayPort")
        self._asset("MON", "Dell", "P2423D 24-inch", "CN0P2423D02", "24-inch QHD IPS, HDMI and DisplayPort")
        self._asset("LAP", "HP", "ProBook 450 G10", "5CD3301A11", "Core i5-1335U, 16 GB RAM, 512 GB SSD, 15.6-inch")
        self._asset("PRJ", "Epson", "EB-X49", "X4Z9001234", "3600 lumens XGA, HDMI and VGA")
        self._asset("PER", "Logitech", "H390 USB headset", None, "USB headset with noise-cancelling microphone")
        old = self._asset(
            "DSK",
            "Lenovo",
            "ThinkCentre M70e",
            "MJ0ABC12",
            "Core i3-8100, 4 GB RAM, 500 GB HDD",
            condition=Condition.POOR,
            acquired_on=date(2018, 3, 12),
        )
        # Give Memory her current desktop through a signed voucher, as the system would.
        self.old_desktop = old

    # -- requests ----------------------------------------------------------------

    def _new(self, kind, requester, title, justification, items, **extra):
        req = Requisition.objects.create(
            kind=kind,
            requester=requester,
            created_by=requester,
            department=requester.department,
            title=title,
            justification=justification,
            campus=requester.campus,
            building=requester.building,
            office=requester.office,
            created_at=timezone.now(),
            **extra,
        )
        for position, (code, description, quantity, notes) in enumerate(items):
            RequisitionItem.objects.create(
                requisition=req,
                category=AssetCategory.objects.get(code=code),
                description=description,
                quantity=quantity,
                requester_notes=notes,
                position=position,
            )
        return req

    def _act(self, req, user, code, **data):
        req.refresh_from_db()
        return services.perform(req, user, code, data=data, password=self.password)

    def _pdf(self, name):
        from django.core.files.uploadedfile import SimpleUploadedFile

        content = (
            b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
            b"trailer<</Root 1 0 R>>\n%%EOF\n"
        )
        return SimpleUploadedFile(name, content, content_type="application/pdf")

    def _requests(self):
        real_now = timezone.now()
        start = timezone.localtime() - timedelta(days=60)
        start = start.replace(hour=8, minute=30, second=0, microsecond=0)
        clock = Clock(start)
        clock.advance()
        with frozen(clock):
            self._story_completed(clock)
            self._story_replacement_with_csm(clock)
            # Anchor the open requests close to today so some are inside their targets.
            clock.jump_to(timezone.localtime(real_now) - timedelta(days=9))
            self._story_awaiting_director(clock)
            clock.jump_to(timezone.localtime(real_now) - timedelta(days=2))
            self._story_allocation_awaiting_signature(clock)
            self._story_returned(clock)
            clock.jump_to(timezone.localtime(real_now) - timedelta(hours=3))
            self._story_new_with_pa(clock)
        if clock.now() > real_now:
            raise CommandError("The demo timeline ran past the present. Shorten the gaps in seed_demo.")

    def _story_completed(self, clock):
        """A full CUT ICT 38 run, from request to the user's signature."""
        memory = self.memory
        req = self._new(
            Kind.EQUIPMENT,
            memory,
            "Laptop for the digital archive project",
            "The digitisation project starts next term. The scanning workstation needs a laptop that can run the "
            "OCR software, which needs 16 GB of memory.",
            [("LAP", "Laptop with at least 16 GB memory", 1, "Runs OCR software for long batches")],
            priority=Priority.ROUTINE,
        )
        self._act(req, memory, "submit")
        clock.advance(hours=5)
        self._act(req, self.farai, "pa_forward", comment="Complete. Forwarding.")
        clock.advance(days=1)
        self._act(
            req,
            self.director,
            "director_forward",
            comment="Supported. Check whether the 2025 laptop order left any spare units first.",
        )
        clock.advance(hours=3)
        self._act(req, self.csm, "csm_assign", technician=self.tech2)
        clock.advance(days=2)
        item = req.items.first()
        self._act(
            req,
            self.tech2,
            "submit_specifications",
            comment="No spare units in stock. Specification follows the 2026 standard laptop.",
            specifications={
                item.pk: {"specification": "Core i7, 16 GB RAM, 512 GB SSD, 14-inch FHD, 3-year warranty", "cost": 1150}
            },
            files=[self._pdf("laptop-spec-sheet.pdf")],
        )
        clock.advance(days=1)
        self._act(req, self.ops, "ops_confirm", comment="Matches the requirement.")
        clock.advance(hours=6)
        self._act(req, self.director, "director_approve")
        clock.advance(hours=20)
        self._act(req, self.tech2, "send_to_procurement", procurement_reference="PR-2026-1183")
        clock.advance(days=5)
        self._act(
            req,
            self.tech2,
            "follow_up",
            comment="Spoke to Procurement. Order placed with the supplier.",
            next_follow_up_on=(clock.now() + timedelta(days=7)).date(),
        )
        clock.advance(days=4)
        self._act(
            req,
            self.procurement,
            "procurement_update",
            procurement_status="Delivered to ICT",
            purchase_order="PO-55120",
            comment="Delivered to ICT stores this morning.",
        )
        clock.advance(hours=4)
        self._act(
            req,
            self.tech2,
            "record_delivery",
            delivery_complete=True,
            assets=[
                {
                    "category": AssetCategory.objects.get(code="LAP"),
                    "make": "Dell",
                    "model": "Latitude 5450",
                    "serial_number": "DL5450ZW77",
                    "asset_tag": "",
                    "specification": "Core i7, 16 GB RAM, 512 GB SSD",
                    "condition": Condition.NEW,
                    "warranty_expires_on": (clock.now() + timedelta(days=1095)).date(),
                    "supplier": "Demo Supplies (Pvt) Ltd",
                    "purchase_order_ref": "PO-55120",
                    "acquired_on": clock.now().date(),
                    "campus": self.main,
                }
            ],
        )
        clock.advance(hours=2)
        req.refresh_from_db()
        laptop = req.delivered_assets.first()
        self._act(
            req,
            self.tech2,
            "raise_voucher",
            asset_ids=[laptop.pk],
            campus=self.main,
            building=memory.building,
            office=memory.office,
        )
        clock.advance(hours=3)
        self._act(req, memory, "acknowledge")

    def _story_replacement_with_csm(self, clock):
        """Memory's old desktop: issued to her, then a replacement request waits for the CSM."""
        clock.advance(days=1)
        memory = self.memory
        alloc = self._new(
            Kind.ALLOCATION,
            memory,
            "Desktop for the circulation desk",
            "The circulation desk has no computer.",
            [("DSK", "Desktop computer", 1, "")],
        )
        self._act(alloc, memory, "submit")
        clock.advance(hours=2)
        self._act(
            alloc,
            self.tech,
            "issue_from_stock",
            asset_ids=[self.old_desktop.pk],
            campus=self.main,
            building=memory.building,
            office=memory.office,
        )
        clock.advance(hours=1)
        self._act(alloc, memory, "acknowledge")
        clock.advance(days=4)
        req = self._new(
            Kind.REPLACEMENT,
            memory,
            "Replace the circulation desk desktop",
            "The desktop takes about 10 minutes to start and freezes when the library system and the scanner run "
            "together. It has 4 GB of memory and a failing hard disk (SMART warnings since September).",
            [("DSK", "Desktop computer", 1, "Needs to run the library system and a barcode scanner")],
            asset_to_replace=self.old_desktop,
            replacement_reason=ReplacementReason.OBSOLETE,
        )
        self._act(req, memory, "submit")

    def _story_awaiting_director(self, clock):
        tendai = self.tendai
        req = self._new(
            Kind.EQUIPMENT,
            tendai,
            "Workstations for the CAD teaching lab",
            "Third-year mechanical students start the CAD module in February. The lab has no machines that run "
            "the current CAD release. We need 3 workstations for group work and 1 projector.",
            [
                ("DSK", "CAD workstation with dedicated graphics", 3, "Must run the current CAD release"),
                ("PRJ", "Ceiling projector", 1, "Lab 4, Engineering Block"),
            ],
            priority=Priority.URGENT,
        )
        self._act(req, tendai, "submit")
        clock.advance(hours=3)
        self._act(req, self.farai, "pa_forward")
        clock.advance(days=1, hours=2)
        self._act(req, self.director, "director_forward", comment="Priority for the new semester. Proceed.")
        clock.advance(hours=4)
        self._act(req, self.csm, "csm_assign", technician=self.tech)
        clock.advance(days=2)
        items = list(req.items.all())
        self._act(
            req,
            self.tech,
            "submit_specifications",
            comment="Projector EB-X49 is in stock but is needed for the Library; quoting a new one.",
            specifications={
                items[0].pk: {
                    "specification": "Core i7, 32 GB RAM, 1 TB NVMe, RTX-class GPU with 8 GB, 27-inch",
                    "cost": 2400,
                },
                items[1].pk: {"specification": "4000 lumens WXGA, short throw, ceiling mount", "cost": 900},
            },
            files=[self._pdf("cad-workstation-spec.pdf"), self._pdf("projector-quotation.pdf")],
        )
        clock.advance(days=1)
        self._act(req, self.ops, "ops_confirm", comment="Confirmed against the CAD vendor's requirements.")

    def _story_allocation_awaiting_signature(self, clock):
        tendai = self.tendai
        req = self._new(
            Kind.ALLOCATION,
            tendai,
            "Second monitor for marking",
            "Marking scanned scripts is faster with two screens.",
            [("MON", "24-inch monitor", 1, "")],
        )
        self._act(req, tendai, "submit")
        clock.advance(hours=1)
        self._act(req, self.tech, "claim")
        clock.advance(hours=2)
        monitor = AssetCategory.objects.get(code="MON").assets.filter(status=AssetStatus.IN_STOCK).first()
        self._act(
            req,
            self.tech,
            "issue_from_stock",
            asset_ids=[monitor.pk],
            campus=self.main,
            building=tendai.building,
            office=tendai.office,
            notes="Delivered with HDMI cable.",
        )

    def _story_returned(self, clock):
        clock.advance(hours=5)
        tendai = self.tendai
        req = self._new(Kind.EQUIPMENT, tendai, "Tablet", "For teaching.", [("TAB", "Tablet", 1, "")])
        self._act(req, tendai, "submit")
        clock.advance(hours=4)
        self._act(
            req,
            self.farai,
            "pa_return",
            comment="Please explain which course the tablet is for and how often you will use it, "
            "so the Director can decide.",
        )

    def _story_new_with_pa(self, clock):
        memory = self.memory
        req = self._new(
            Kind.EQUIPMENT,
            memory,
            "Barcode scanners for the new branch library",
            "The branch library opens in March with two issue points.",
            [("PER", "USB barcode scanner", 2, "Compatible with the library system")],
        )
        self._act(req, memory, "submit")
