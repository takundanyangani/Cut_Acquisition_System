from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from .models import Delegation, LoginAttempt, RoleGrant, User


class RoleGrantInline(admin.TabularInline):
    model = RoleGrant
    fk_name = "user"
    extra = 0
    fields = ["role", "granted_by", "granted_at"]
    readonly_fields = ["granted_by", "granted_at"]


class StaffCreationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ["email", "first_name", "last_name"]


class StaffChangeForm(UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = StaffChangeForm
    add_form = StaffCreationForm
    inlines = [RoleGrantInline]
    ordering = ["first_name", "last_name"]
    list_display = ["email", "display_name", "staff_number", "department", "is_active", "roles_summary"]
    list_filter = ["is_active", "role_grants__role", "department", "campus"]
    search_fields = ["email", "first_name", "last_name", "staff_number"]
    readonly_fields = ["last_login", "date_joined", "password_changed_at"]
    fieldsets = [
        (None, {"fields": ["email", "password"]}),
        ("Person", {"fields": ["title", "first_name", "last_name", "staff_number", "job_title"]}),
        ("Location", {"fields": ["department", "campus", "building", "office", "phone_extension"]}),
        (
            "Access",
            {
                "fields": ["is_active", "must_change_password", "is_staff", "is_superuser"],
                "description": (
                    "Workflow roles are set in the Roles table below. "
                    "Staff status opens this administration site and should be limited to system administrators."
                ),
            },
        ),
        ("History", {"fields": ["last_login", "date_joined", "password_changed_at"]}),
    ]
    add_fieldsets = [
        (
            None,
            {
                "classes": ["wide"],
                "fields": ["email", "first_name", "last_name", "password1", "password2"],
                "description": "The user will be asked to choose a new password at first sign-in.",
            },
        )
    ]

    @admin.display(description="Roles")
    def roles_summary(self, obj):
        return ", ".join(obj.role_labels()) or "Requester only"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.must_change_password = True
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for instance in instances:
            if isinstance(instance, RoleGrant) and instance.granted_by_id is None:
                instance.granted_by = request.user
            instance.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()


@admin.register(Delegation)
class DelegationAdmin(admin.ModelAdmin):
    list_display = ["delegate", "role", "delegator", "starts_at", "ends_at", "status", "reason"]
    list_filter = ["role"]
    search_fields = ["delegate__email", "delegator__email", "reason"]
    readonly_fields = ["created_by", "created_at", "revoked_by", "revoked_at"]
    autocomplete_fields = ["delegator", "delegate"]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    list_display = ["created_at", "identifier", "kind", "successful", "ip_address"]
    list_filter = ["successful", "kind"]
    search_fields = ["identifier", "ip_address"]
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
