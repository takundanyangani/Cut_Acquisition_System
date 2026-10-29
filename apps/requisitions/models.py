import hashlib
import json
import os
import uuid
from datetime import UTC

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone


class Kind(models.TextChoices):
    ALLOCATION = "ALC", "Allocation from ICT stock"
    EQUIPMENT = "EQR", "Equipment request"
    REPLACEMENT = "RPL", "Replacement request"


KIND_PROCEDURE = {
    Kind.ALLOCATION: ("CUT ICT 40", "ICT Asset Allocation"),
    Kind.EQUIPMENT: ("CUT ICT 38", "Equipment Request"),
    Kind.REPLACEMENT: ("CUT ICT 42", "Asset Acquisition (replacement)"),
}


class State(models.TextChoices):
    DRAFT = "draft", "Draft"
    RETURNED = "returned", "Returned for changes"
    PA_REVIEW = "pa_review", "Personal Advisor review"
    DIRECTOR_REVIEW = "director_review", "ICT Director review"
    CSM_REVIEW = "csm_review", "Client Services Manager review"
    TECHNICIAN_REVIEW = "technician_review", "Technician assessment"
    OPS_REVIEW = "ops_review", "Operations Manager cross-check"
    DIRECTOR_APPROVAL = "director_approval", "ICT Director approval"
    APPROVED = "approved", "Approved, to be sent to Procurement"
    WITH_PROCUREMENT = "with_procurement", "With Procurement"
    READY_FOR_ISSUE = "ready_for_issue", "Ready for issue"
    AWAITING_ACK = "awaiting_ack", "Awaiting receipt signature"
    COMPLETED = "completed", "Completed"
    DECLINED = "declined", "Declined"
    CANCELLED = "cancelled", "Cancelled"
    CONVERTED = "converted", "Converted to equipment request"


TERMINAL_STATES = frozenset({State.COMPLETED, State.DECLINED, State.CANCELLED, State.CONVERTED})
OPEN_STATES = frozenset(s for s in State if s not in TERMINAL_STATES and s != State.DRAFT)


class Priority(models.TextChoices):
    ROUTINE = "routine", "Routine"
    URGENT = "urgent", "Urgent"


class ReplacementReason(models.TextChoices):
    FAULTY = "faulty", "Faulty beyond economic repair"
    OBSOLETE = "obsolete", "Too old or slow for current work"
    DAMAGED = "damaged", "Physically damaged"
    LOST = "lost", "Lost or stolen"
    OTHER = "other", "Other reason"


class Route(models.TextChoices):
    STOCK = "stock", "Issued from ICT stock"
    PROCUREMENT = "procurement", "Bought through Procurement"


class RequisitionQuerySet(models.QuerySet):
    def active(self):
        return self.filter(archived_at__isnull=True)

    def open(self):
        return self.filter(state__in=OPEN_STATES)

    def visible_to(self, user):
        from apps.accounts.models import ICT_STAFF_ROLES, Role

        if not user.is_authenticated:
            return self.none()
        own = models.Q(requester=user)
        if user.is_superuser or user.has_any_role(ICT_STAFF_ROLES):
            return self.filter(own | ~models.Q(state=State.DRAFT))
        if user.has_role(Role.PROCUREMENT_OFFICER):
            return self.filter(own | models.Q(sent_to_procurement_at__isnull=False))
        return self.filter(own)


class Requisition(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    reference = models.CharField(max_length=20, unique=True, null=True, blank=True)
    kind = models.CharField(max_length=3, choices=Kind.choices)
    title = models.CharField("what do you need?", max_length=160)
    justification = models.TextField("why do you need it?")
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.ROUTINE)
    needed_by = models.DateField(null=True, blank=True)

    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requisitions")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    department = models.ForeignKey("core.Department", null=True, blank=True, on_delete=models.PROTECT)
    campus = models.ForeignKey("core.Campus", null=True, on_delete=models.PROTECT)
    building = models.CharField(max_length=120)
    office = models.CharField("office or room", max_length=60, blank=True)

    # Replacement requests (CUT ICT 42)
    asset_to_replace = models.ForeignKey(
        "assets.Asset", null=True, blank=True, on_delete=models.PROTECT, related_name="replacement_requests"
    )
    replaced_item_description = models.CharField(
        "machine to replace",
        max_length=255,
        blank=True,
        help_text="Make, model and serial number, if the machine is not in your list.",
    )
    replacement_reason = models.CharField(max_length=12, choices=ReplacementReason.choices, blank=True)

    # Workflow
    state = models.CharField(max_length=20, choices=State.choices, default=State.DRAFT, db_index=True)
    state_entered_at = models.DateTimeField(default=timezone.now)
    state_due_at = models.DateTimeField(null=True, blank=True)
    returned_from_state = models.CharField(max_length=20, choices=State.choices, blank=True)
    assigned_technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assigned_requisitions",
    )
    csm_recommends = models.BooleanField(null=True, blank=True)
    fulfilment_route = models.CharField(max_length=12, choices=Route.choices, blank=True)
    procurement_reference = models.CharField(max_length=60, blank=True)
    procurement_status = models.CharField(max_length=160, blank=True)
    expected_delivery_on = models.DateField(null=True, blank=True)
    sent_to_procurement_at = models.DateTimeField(null=True, blank=True)
    next_follow_up_on = models.DateField(null=True, blank=True)
    delivery_completed_at = models.DateTimeField(null=True, blank=True)
    converted_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="converted_requests"
    )

    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_reminder_at = models.DateTimeField(null=True, blank=True)

    version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = RequisitionQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["kind", "state"])]
        verbose_name = "request"

    def __str__(self):
        return f"{self.reference or 'Draft'}: {self.title}"

    def get_absolute_url(self):
        return reverse("requisitions:detail", args=[self.public_id])

    @property
    def procedure_number(self):
        return KIND_PROCEDURE[Kind(self.kind)][0]

    @property
    def procedure_title(self):
        return KIND_PROCEDURE[Kind(self.kind)][1]

    @property
    def display_reference(self):
        return self.reference or "Draft"

    @property
    def is_terminal(self):
        return self.state in TERMINAL_STATES

    @property
    def is_open(self):
        return self.state in OPEN_STATES

    @property
    def is_editable(self):
        return self.state in {State.DRAFT, State.RETURNED} and self.archived_at is None

    @property
    def is_overdue(self):
        return bool(self.state_due_at and self.is_open and self.state_due_at < timezone.now())

    @property
    def follow_up_due(self):
        return bool(
            self.state == State.WITH_PROCUREMENT
            and self.next_follow_up_on
            and self.next_follow_up_on <= timezone.localdate()
        )

    @property
    def location_text(self):
        parts = [str(self.campus) if self.campus_id else "", self.building, self.office]
        return ", ".join(p for p in parts if p)

    @property
    def items_summary(self):
        items = list(self.items.all())
        return "; ".join(f"{i.quantity} x {i.description}" for i in items)

    @property
    def total_quantity(self):
        return sum(i.quantity for i in self.items.all())

    @property
    def active_attachments(self):
        return [a for a in self.attachments.all() if a.removed_at is None]

    @property
    def open_voucher(self):
        from apps.assets.models import IssueVoucher

        return self.vouchers.filter(status=IssueVoucher.Status.AWAITING).first()


class RequisitionItem(models.Model):
    requisition = models.ForeignKey(Requisition, on_delete=models.CASCADE, related_name="items")
    category = models.ForeignKey("assets.AssetCategory", on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=160)
    quantity = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(200)])
    requester_notes = models.TextField("what it will be used for", blank=True)
    recommended_specification = models.TextField(blank=True)
    estimated_unit_cost = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]
        verbose_name = "request item"

    def __str__(self):
        return f"{self.quantity} x {self.description}"

    @property
    def estimated_total(self):
        if self.estimated_unit_cost is None:
            return None
        return self.estimated_unit_cost * self.quantity


def attachment_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    now = timezone.now()
    return f"requisitions/{now:%Y}/{now:%m}/{uuid.uuid4().hex}{ext}"


class Attachment(models.Model):
    class Kind(models.TextChoices):
        SPEC_SHEET = "spec_sheet", "Specification sheet"
        QUOTATION = "quotation", "Quotation"
        SUPPORTING = "supporting", "Supporting document"
        DELIVERY_NOTE = "delivery_note", "Delivery note"
        POLICE_REPORT = "police_report", "Police report"
        OTHER = "other", "Other"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    requisition = models.ForeignKey(Requisition, on_delete=models.CASCADE, related_name="attachments")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.SUPPORTING)
    file = models.FileField(upload_to=attachment_path, max_length=255)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=120)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(default=timezone.now)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    class Meta:
        ordering = ["uploaded_at"]

    def __str__(self):
        return self.original_name

    def get_absolute_url(self):
        return reverse("requisitions:attachment", args=[self.public_id])


class AuditEntry(models.Model):
    """One line of the routing slip. Entries are append-only and chained:
    each stores the SHA-256 of its own content plus the previous entry's hash,
    so editing or deleting a row breaks the chain (see verify_chain)."""

    requisition = models.ForeignKey(Requisition, on_delete=models.PROTECT, related_name="audit_entries")
    seq = models.PositiveIntegerField()
    action = models.CharField(max_length=40)
    label = models.CharField(max_length=160)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    actor_name = models.CharField(max_length=200)
    actor_role = models.CharField(max_length=40, blank=True)
    actor_role_label = models.CharField(max_length=80, blank=True)
    on_behalf_of = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    on_behalf_of_name = models.CharField(max_length=200, blank=True)
    from_state = models.CharField(max_length=20, blank=True)
    to_state = models.CharField(max_length=20, blank=True)
    comment = models.TextField(blank=True)
    signed = models.BooleanField(default=False)
    sop_clause = models.CharField(max_length=60, blank=True)
    data = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    prev_hash = models.CharField(max_length=64)
    entry_hash = models.CharField(max_length=64)

    GENESIS = "0" * 64

    class Meta:
        ordering = ["requisition", "seq"]
        constraints = [models.UniqueConstraint(fields=["requisition", "seq"], name="unique_audit_seq")]
        verbose_name_plural = "audit entries"

    def __str__(self):
        return f"{self.requisition.display_reference} #{self.seq}: {self.label}"

    def canonical_payload(self):
        return json.dumps(
            {
                "requisition": self.requisition_id,
                "seq": self.seq,
                "action": self.action,
                "label": self.label,
                "actor": self.actor_id,
                "actor_name": self.actor_name,
                "actor_role": self.actor_role,
                "on_behalf_of": self.on_behalf_of_id,
                "on_behalf_of_name": self.on_behalf_of_name,
                "from_state": self.from_state,
                "to_state": self.to_state,
                "comment": self.comment,
                "signed": self.signed,
                "sop_clause": self.sop_clause,
                "data": self.data,
                "created_at": self.created_at.astimezone(UTC).isoformat(),
                "prev_hash": self.prev_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def compute_hash(self):
        return hashlib.sha256(self.canonical_payload().encode("utf-8")).hexdigest()

    @property
    def short_hash(self):
        return f"{self.entry_hash[:6]}…{self.entry_hash[-4:]}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("Audit entries cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit entries cannot be deleted.")


def verify_chain(requisition):
    """Return a list of problems with a request's audit chain. Empty means intact."""
    problems = []
    previous = AuditEntry.GENESIS
    expected_seq = 1
    for entry in requisition.audit_entries.order_by("seq"):
        if entry.seq != expected_seq:
            problems.append(f"Entry {expected_seq} is missing; found {entry.seq}.")
            expected_seq = entry.seq
        if entry.prev_hash != previous:
            problems.append(f"Entry {entry.seq} does not link to the entry before it.")
        if entry.compute_hash() != entry.entry_hash:
            problems.append(f"Entry {entry.seq} has been altered since it was written.")
        previous = entry.entry_hash
        expected_seq += 1
    return problems
