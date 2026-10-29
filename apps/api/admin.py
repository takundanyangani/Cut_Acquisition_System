from django.contrib import admin
from django.utils import timezone

from .models import ApiKey


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ["name", "prefix", "created_at", "last_used_at", "revoked_at"]
    readonly_fields = ["name", "prefix", "key_hash", "created_by", "created_at", "last_used_at", "revoked_at"]
    actions = ["revoke"]

    def has_add_permission(self, request):
        # Keys are created with `manage.py create_api_key` so the secret is shown once.
        return False

    @admin.action(description="Revoke the selected keys")
    def revoke(self, request, queryset):
        count = queryset.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
        self.message_user(request, f"{count} key(s) revoked.")
