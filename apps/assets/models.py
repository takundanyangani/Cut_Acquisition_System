import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone


class AssetCategory(models.Model):
    name = models.CharField(max_length=80, unique=True)
    code = models.CharField(max_length=10, unique=True, help_text="Short code, for example LAP or PRJ.")
    description = models.CharField(max_length=255, blank=True)
    requires_serial = models.BooleanField(
        default=True, help_text="Untick for items without serial numbers, such as cables."
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "asset categories"

    def __str__(self):
        return self.name


class AssetStatus(models.TextChoices):
    IN_STOCK = "in_stock", "In stock"
    RESERVED = "reserved", "On voucher, awaiting signature"
    ALLOCATED = "allocated", "Allocated"
    RETURNED = "returned", "Returned, awaiting assessment"
    IN_REPAIR = "in_repair", "In repair"
    LOST = "lost", "Lost or stolen"
    DISPOSED = "disposed", "Disposed"


class Condition(models.TextChoices):
    NEW = "new", "New"
    GOOD = "good", "Good"
    FAIR = "fair", "Fair"
    POOR = "poor", "Poor"
    FAULTY = "faulty", "Faulty"


# Status changes a technician may record by hand. Reserved and Allocated are
# set only by issue vouchers, so every hand-over has a signed record.
MANUAL_STATUS_CHANGES = {
    AssetStatus.IN_STOCK: {AssetStatus.IN_REPAIR, AssetStatus.LOST, AssetStatus.DISPOSED},
    AssetStatus.ALLOCATED: {AssetStatus.RETURNED, AssetStatus.LOST},
    AssetStatus.RETURNED: {AssetStatus.IN_STOCK, AssetStatus.IN_REPAIR, AssetStatus.DISPOSED},
    AssetStatus.IN_REPAIR: {AssetStatus.IN_STOCK, AssetStatus.DISPOSED},
    AssetStatus.LOST: {AssetStatus.RETURNED},
    AssetStatus.RESERVED: set(),
    AssetStatus.DISPOSED: set(),
}


def normalise_serial(value):
    value = (value or "").strip().upper()
    return value or None


class Asset(models.Model):
    asset_tag = models.CharField(max_length=40, unique=True)
    serial_number = models.CharField(max_length=80, unique=True, null=True, blank=True)
    category = models.ForeignKey(AssetCategory, on_delete=models.PROTECT, related_name="assets")
    make = models.CharField(max_length=80)
    model = models.CharField(max_length=120)
    specification = models.TextField(help_text="Processor, memory, storage, screen size, and similar.")
    condition = models.CharField(max_length=10, choices=Condition.choices, default=Condition.NEW)
    status = models.CharField(max_length=12, choices=AssetStatus.choices, default=AssetStatus.IN_STOCK, db_index=True)
    holder = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="assets_held"
    )
    campus = models.ForeignKey("core.Campus", null=True, blank=True, on_delete=models.PROTECT)
    building = models.CharField(max_length=120, blank=True)
    office = models.CharField("office or room", max_length=60, blank=True)
    supplier = models.CharField(max_length=160, blank=True)
    purchase_order_ref = models.CharField("purchase order", max_length=60, blank=True)
    acquired_on = models.DateField(null=True, blank=True)
    warranty_expires_on = models.DateField(null=True, blank=True)
    source_requisition = models.ForeignKey(
        "requisitions.Requisition",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="delivered_assets",
        help_text="The request whose procurement delivered this asset.",
    )
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset_tag"]

    def __str__(self):
        return f"{self.asset_tag} {self.make} {self.model}"

    def save(self, *args, **kwargs):
        self.serial_number = normalise_serial(self.serial_number)
        self.asset_tag = (self.asset_tag or "").strip().upper()
        super().save(*args, **kwargs)

    def clean(self):
        if self.category_id and self.category.requires_serial and not normalise_serial(self.serial_number):
            raise ValidationError({"serial_number": f"{self.category} items need a serial number."})

    def get_absolute_url(self):
        return reverse("assets:detail", args=[self.asset_tag])

    @property
    def make_model(self):
        return f"{self.make} {self.model}".strip()

    @property
    def location_text(self):
        parts = [str(self.campus) if self.campus_id else "", self.building, self.office]
        return ", ".join(p for p in parts if p)


class MovementKind(models.TextChoices):
    REGISTERED = "registered", "Registered in stock"
    IMPORTED = "imported", "Imported from the previous register"
    RECEIVED = "received", "Received from supplier"
    RESERVED = "reserved", "Placed on issue voucher"
    RELEASED = "released", "Released from voucher back to stock"
    ISSUED = "issued", "Issued to user"
    RETURNED = "returned", "Returned to ICT"
    STATUS_CHANGED = "status_changed", "Status changed"


class AssetMovement(models.Model):
    """Append-only history of every change in custody or status."""

    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=20, choices=MovementKind.choices)
    from_status = models.CharField(max_length=12, choices=AssetStatus.choices, blank=True)
    to_status = models.CharField(max_length=12, choices=AssetStatus.choices, blank=True)
    from_holder = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    to_holder = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    location_text = models.CharField(max_length=300, blank=True)
    voucher = models.ForeignKey(
        "IssueVoucher", null=True, blank=True, on_delete=models.PROTECT, related_name="movements"
    )
    requisition = models.ForeignKey(
        "requisitions.Requisition", null=True, blank=True, on_delete=models.PROTECT, related_name="asset_movements"
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.asset.asset_tag}: {self.get_kind_display()}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("Asset movements cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Asset movements cannot be deleted.")


class IssueVoucher(models.Model):
    """CUT ICT 40 issue voucher: machine details the user signs for."""

    class Status(models.TextChoices):
        AWAITING = "awaiting", "Awaiting recipient's signature"
        ACKNOWLEDGED = "acknowledged", "Signed by recipient"
        DISPUTED = "disputed", "Disputed by recipient"
        WITHDRAWN = "withdrawn", "Withdrawn by ICT"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    number = models.CharField(max_length=20, unique=True)
    requisition = models.ForeignKey("requisitions.Requisition", on_delete=models.PROTECT, related_name="vouchers")
    issued_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="vouchers")
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    campus = models.ForeignKey("core.Campus", on_delete=models.PROTECT)
    building = models.CharField(max_length=120)
    office = models.CharField("office or room", max_length=60, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.AWAITING, db_index=True)

    # Replacement requests (CUT ICT 42): what happened to the old machine.
    replaced_asset = models.ForeignKey(
        Asset, null=True, blank=True, on_delete=models.PROTECT, related_name="replaced_on_vouchers"
    )
    replaced_asset_collected = models.BooleanField(null=True, blank=True)
    replaced_asset_condition = models.CharField(max_length=10, choices=Condition.choices, blank=True)
    replaced_asset_note = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledgement_text = models.TextField(blank=True)
    acknowledged_ip = models.GenericIPAddressField(null=True, blank=True)
    disputed_at = models.DateTimeField(null=True, blank=True)
    dispute_reason = models.TextField(blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawn_reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.number

    def get_absolute_url(self):
        return reverse("assets:voucher_detail", args=[self.number])

    @property
    def location_text(self):
        parts = [str(self.campus), self.building, self.office]
        return ", ".join(p for p in parts if p)


class IssueVoucherLine(models.Model):
    """Snapshot of the machine details at issue, so the signed record never changes."""

    voucher = models.ForeignKey(IssueVoucher, on_delete=models.PROTECT, related_name="lines")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="voucher_lines")
    asset_tag = models.CharField(max_length=40)
    serial_number = models.CharField(max_length=80, blank=True)
    category_name = models.CharField(max_length=80)
    make_model = models.CharField(max_length=210)
    specification = models.TextField()
    condition = models.CharField(max_length=10, choices=Condition.choices)

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return f"{self.voucher.number}: {self.asset_tag}"
