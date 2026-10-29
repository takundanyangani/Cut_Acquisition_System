from django.contrib import admin
from django.contrib.auth.models import Group

from .models import Campus, Department, Holiday, Notification, OutboundEmail

admin.site.site_header = "ICT Acquisitions administration"
admin.site.site_title = "ICT Acquisitions admin"
admin.site.index_title = "Master data and accounts"
admin.site.site_url = "/"


def _superusers_only(request):
    # Only superusers reach this site. Workflow roles never grant it, so a
    # Director or CSM cannot edit records behind the workflow.
    return request.user.is_active and request.user.is_superuser


admin.site.has_permission = _superusers_only

# Workflow roles live on each user (Roles inline), not in Django groups.
admin.site.unregister(Group)


@admin.register(Campus)
class CampusAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "is_active"]
    list_editable = ["is_active"]


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "is_active"]
    list_editable = ["is_active"]
    search_fields = ["name", "code"]


@admin.register(Holiday)
class HolidayAdmin(admin.ModelAdmin):
    list_display = ["date", "name"]
    date_hierarchy = "date"


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["created_at", "recipient", "title", "read_at"]
    search_fields = ["recipient__email", "title"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(OutboundEmail)
class OutboundEmailAdmin(admin.ModelAdmin):
    list_display = ["created_at", "to", "subject", "status", "attempts", "sent_at"]
    list_filter = ["status"]
    search_fields = ["to", "subject"]
    readonly_fields = ["to", "subject", "body", "attempts", "last_error", "created_at", "claimed_at", "sent_at"]
    actions = ["retry"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Retry sending the selected messages")
    def retry(self, request, queryset):
        updated = queryset.exclude(status=OutboundEmail.Status.SENT).update(
            status=OutboundEmail.Status.PENDING, attempts=0
        )
        self.message_user(request, f"{updated} message(s) queued again. The deliver_emails job will send them.")
