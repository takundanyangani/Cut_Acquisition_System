from django.contrib import admin

from .models import Asset, AssetCategory, AssetMovement, IssueVoucher, IssueVoucherLine


@admin.register(AssetCategory)
class AssetCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "requires_serial", "is_active"]
    list_editable = ["is_active"]
    search_fields = ["name", "code"]


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ["asset_tag", "serial_number", "category", "make", "model", "status", "holder"]
    list_filter = ["status", "category", "condition"]
    search_fields = ["asset_tag", "serial_number", "make", "model", "holder__email"]
    # Custody and status change only through vouchers and the asset page,
    # which write the movement log.
    readonly_fields = ["asset_tag", "status", "holder", "source_requisition", "created_by", "created_at"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AssetMovement)
class AssetMovementAdmin(ReadOnlyAdmin):
    list_display = ["created_at", "asset", "kind", "from_status", "to_status", "to_holder", "performed_by"]
    list_filter = ["kind"]
    search_fields = ["asset__asset_tag", "asset__serial_number"]
    date_hierarchy = "created_at"


class VoucherLineInline(admin.TabularInline):
    model = IssueVoucherLine
    extra = 0
    can_delete = False
    readonly_fields = [
        "asset",
        "asset_tag",
        "serial_number",
        "category_name",
        "make_model",
        "specification",
        "condition",
    ]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(IssueVoucher)
class IssueVoucherAdmin(ReadOnlyAdmin):
    list_display = ["number", "requisition", "issued_to", "issued_by", "status", "created_at", "acknowledged_at"]
    list_filter = ["status"]
    search_fields = ["number", "requisition__reference", "issued_to__email"]
    inlines = [VoucherLineInline]
