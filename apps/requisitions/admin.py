from django.contrib import admin

from .models import Attachment, AuditEntry, Requisition, RequisitionItem


class ReadOnlyMixin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ItemInline(ReadOnlyMixin, admin.TabularInline):
    model = RequisitionItem
    extra = 0
    fields = ["category", "description", "quantity", "recommended_specification", "estimated_unit_cost"]
    readonly_fields = fields


class AttachmentInline(ReadOnlyMixin, admin.TabularInline):
    model = Attachment
    extra = 0
    fields = ["kind", "original_name", "size", "uploaded_by", "uploaded_at", "removed_at"]
    readonly_fields = fields


class AuditInline(ReadOnlyMixin, admin.TabularInline):
    model = AuditEntry
    extra = 0
    fields = [
        "seq",
        "created_at",
        "label",
        "actor_name",
        "actor_role_label",
        "on_behalf_of_name",
        "from_state",
        "to_state",
        "signed",
        "comment",
    ]
    readonly_fields = fields
    ordering = ["seq"]


@admin.register(Requisition)
class RequisitionAdmin(ReadOnlyMixin, admin.ModelAdmin):
    """Read-only on purpose. Stages change only through the workflow engine,
    which signs, locks and logs each step."""

    list_display = ["reference", "kind", "title", "requester", "state", "submitted_at", "archived_at"]
    list_filter = ["kind", "state", "priority"]
    search_fields = ["reference", "title", "requester__email", "procurement_reference"]
    date_hierarchy = "submitted_at"
    inlines = [ItemInline, AttachmentInline, AuditInline]
