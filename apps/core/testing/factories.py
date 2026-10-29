"""Shared builders for the test suite."""

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import Role, RoleGrant, User
from apps.assets import services as asset_services
from apps.assets.models import AssetCategory, Condition
from apps.core.models import Campus, Department
from apps.requisitions import services
from apps.requisitions.models import Kind, Requisition, RequisitionItem

PASSWORD = "Correct-Horse-2026"

PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def pdf(name="spec.pdf"):
    return SimpleUploadedFile(name, PDF_BYTES, content_type="application/pdf")


class World:
    """One person per role, a campus, a department and some stock."""

    def __init__(self):
        self.campus = Campus.objects.create(name="Main Campus", code="MAIN")
        self.dept = Department.objects.create(name="Engineering", code="ENG")
        self.laptop_cat = AssetCategory.objects.get(code="LAP")
        self.monitor_cat = AssetCategory.objects.get(code="MON")
        self.requester = self.user("req", "Rita", "Requester")
        self.other = self.user("other", "Olly", "Other")
        self.pa = self.user("pa", "Pat", "Advisor", Role.PERSONAL_ADVISOR)
        self.director = self.user("dir", "Dana", "Director", Role.ICT_DIRECTOR)
        self.csm = self.user("csm", "Chris", "Manager", Role.CLIENT_SERVICES_MANAGER)
        self.tech = self.user("tech", "Tino", "Tech", Role.TECHNICIAN)
        self.tech2 = self.user("tech2", "Tari", "Tech", Role.TECHNICIAN)
        self.ops = self.user("ops", "Olga", "Ops", Role.OPERATIONS_MANAGER)
        self.procurement = self.user("proc", "Pele", "Buyer", Role.PROCUREMENT_OFFICER)
        self.auditor = self.user("aud", "Audrey", "Auditor", Role.AUDITOR)

    def user(self, local, first, last, *roles):
        user = User.objects.create_user(
            email=f"{local}@test.cut.local",
            password=PASSWORD,
            first_name=first,
            last_name=last,
            department=self.dept,
            campus=self.campus,
            building="Block A",
            office="A1",
        )
        for role in roles:
            RoleGrant.objects.create(user=user, role=role)
        return user

    def stock(self, category=None, serial="SN-1", **extra):
        return asset_services.register_asset(
            performed_by=self.tech,
            category=category or self.laptop_cat,
            make=extra.pop("make", "HP"),
            model=extra.pop("model", "ProBook"),
            serial_number=serial,
            specification=extra.pop("specification", "i5, 16 GB"),
            condition=extra.pop("condition", Condition.NEW),
            campus=self.campus,
            building="Stores",
            **extra,
        )

    def draft(self, kind=Kind.EQUIPMENT, requester=None, items=None, **extra):
        requester = requester or self.requester
        req = Requisition.objects.create(
            kind=kind,
            requester=requester,
            created_by=requester,
            department=requester.department,
            title=extra.pop("title", "Laptop for teaching"),
            justification=extra.pop("justification", "Needed for lectures."),
            campus=self.campus,
            building="Block A",
            office="A1",
            **extra,
        )
        for position, (category, description, quantity) in enumerate(items or [(self.laptop_cat, "Laptop", 1)]):
            RequisitionItem.objects.create(
                requisition=req, category=category, description=description, quantity=quantity, position=position
            )
        return req

    def act(self, req, user, code, **data):
        req.refresh_from_db()
        return services.perform(req, user, code, data=data, password=data.pop("password", PASSWORD))

    def specs(self, req, files=True, cost=500):
        return {
            "specifications": {
                i.pk: {"specification": "Core i5, 16 GB, 512 GB SSD", "cost": cost} for i in req.items.all()
            },
            "files": [pdf()] if files else [],
        }

    def eqr_to_approval(self, req=None):
        """Drive an equipment request to the Director's final approval stage."""
        req = req or self.draft()
        self.act(req, self.requester, "submit")
        self.act(req, self.pa, "pa_forward")
        self.act(req, self.director, "director_forward", comment="Go ahead")
        self.act(req, self.csm, "csm_assign", technician=self.tech)
        req.refresh_from_db()
        self.act(req, self.tech, "submit_specifications", **self.specs(req))
        self.act(req, self.ops, "ops_confirm")
        req.refresh_from_db()
        return req
