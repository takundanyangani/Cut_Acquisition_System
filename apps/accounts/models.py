from dataclasses import dataclass

from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    PERSONAL_ADVISOR = "personal_advisor", "Personal Advisor"
    ICT_DIRECTOR = "ict_director", "ICT Director"
    CLIENT_SERVICES_MANAGER = "csm", "Client Services Manager"
    TECHNICIAN = "technician", "Help Desk Technician"
    OPERATIONS_MANAGER = "operations_manager", "Operations Manager"
    PROCUREMENT_OFFICER = "procurement_officer", "Procurement Officer"
    AUDITOR = "auditor", "Quality Auditor"
    SYSTEM_ADMIN = "system_admin", "System Administrator"


# Roles whose holders can read every submitted request.
ICT_STAFF_ROLES = frozenset(
    {
        Role.PERSONAL_ADVISOR,
        Role.ICT_DIRECTOR,
        Role.CLIENT_SERVICES_MANAGER,
        Role.TECHNICIAN,
        Role.OPERATIONS_MANAGER,
        Role.AUDITOR,
        Role.SYSTEM_ADMIN,
    }
)

# Approval roles that can be handed to an acting officer for a period.
DELEGABLE_ROLES = frozenset(
    {
        Role.PERSONAL_ADVISOR,
        Role.ICT_DIRECTOR,
        Role.CLIENT_SERVICES_MANAGER,
        Role.OPERATIONS_MANAGER,
    }
)

# Roles that see management reports and exports.
REPORT_ROLES = frozenset(
    {
        Role.ICT_DIRECTOR,
        Role.CLIENT_SERVICES_MANAGER,
        Role.OPERATIONS_MANAGER,
        Role.AUDITOR,
        Role.SYSTEM_ADMIN,
    }
)


@dataclass(frozen=True)
class Capacity:
    """The role a user acts in, and the person they act for when delegated."""

    role: str
    on_behalf_of: "User | None" = None

    @property
    def role_label(self):
        if self.role == "requester":
            return "Requester"
        return Role(self.role).label


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, email):
        return self.get(email__iexact=email)

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("An email address is required")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True or extra_fields.get("is_superuser") is not True:
            raise ValueError("A superuser needs is_staff=True and is_superuser=True")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None
    email = models.EmailField("email address", unique=True)
    title = models.CharField(max_length=20, blank=True, help_text="For example Dr, Eng, Mr, Mrs, Ms, Prof.")
    staff_number = models.CharField(max_length=30, unique=True, null=True, blank=True)
    job_title = models.CharField(max_length=120, blank=True)
    department = models.ForeignKey(
        "core.Department", null=True, blank=True, on_delete=models.PROTECT, related_name="staff"
    )
    campus = models.ForeignKey("core.Campus", null=True, blank=True, on_delete=models.PROTECT, related_name="staff")
    building = models.CharField(max_length=120, blank=True)
    office = models.CharField("office or room", max_length=60, blank=True)
    phone_extension = models.CharField(max_length=20, blank=True)
    must_change_password = models.BooleanField(
        default=False, help_text="Ask the user to choose a new password at the next sign-in."
    )
    password_changed_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    objects = UserManager()

    class Meta:
        ordering = ["first_name", "last_name"]

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        if self.staff_number is not None:
            self.staff_number = self.staff_number.strip().upper() or None
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        name = " ".join(part for part in [self.title, self.first_name, self.last_name] if part)
        return name or self.email

    @property
    def short_name(self):
        return self.first_name or self.email

    @property
    def initials(self):
        letters = (self.first_name[:1] + self.last_name[:1]).upper()
        return letters or self.email[:1].upper()

    @property
    def location_text(self):
        parts = [str(self.campus) if self.campus_id else "", self.building, self.office]
        return ", ".join(p for p in parts if p)

    # -- roles ---------------------------------------------------------------

    def _role_cache(self):
        if not hasattr(self, "_cached_roles"):
            self._cached_roles = set(self.role_grants.values_list("role", flat=True)) if self.pk else set()
        return self._cached_roles

    def _delegation_cache(self):
        if not hasattr(self, "_cached_delegations"):
            delegations = {}
            if self.pk:
                for d in Delegation.objects.active().filter(delegate=self).select_related("delegator"):
                    if d.delegator.is_active and d.role in d.delegator.direct_roles:
                        delegations.setdefault(d.role, d.delegator)
            self._cached_delegations = delegations
        return self._cached_delegations

    def clear_role_cache(self):
        for attr in ("_cached_roles", "_cached_delegations"):
            if hasattr(self, attr):
                delattr(self, attr)

    @property
    def direct_roles(self):
        return frozenset(self._role_cache())

    @property
    def delegated_roles(self):
        """Mapping of role to the person who delegated it, for active delegations."""
        return dict(self._delegation_cache())

    @property
    def all_roles(self):
        return frozenset(self._role_cache() | set(self._delegation_cache()))

    def has_role(self, role):
        if not self.is_active:
            return False
        return role in self._role_cache() or role in self._delegation_cache()

    def has_any_role(self, roles):
        return any(self.has_role(r) for r in roles)

    def capacity_for(self, role):
        """How this user would act in `role`: directly, or for a delegator."""
        if not self.is_active:
            return None
        if role in self._role_cache():
            return Capacity(role=role)
        delegator = self._delegation_cache().get(role)
        if delegator is not None:
            return Capacity(role=role, on_behalf_of=delegator)
        return None

    @property
    def is_ict_staff(self):
        return self.has_any_role(ICT_STAFF_ROLES)

    @property
    def can_view_reports(self):
        return self.has_any_role(REPORT_ROLES)

    @property
    def is_system_admin(self):
        return self.is_superuser or self.has_role(Role.SYSTEM_ADMIN)

    def role_labels(self):
        return [Role(r).label for r in sorted(self._role_cache())]


class RoleGrant(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="role_grants")
    role = models.CharField(max_length=30, choices=Role.choices)
    granted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    granted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "role"], name="unique_role_per_user")]
        ordering = ["role"]
        verbose_name = "role"

    def __str__(self):
        return f"{self.user} as {self.get_role_display()}"


class DelegationQuerySet(models.QuerySet):
    def active(self, at=None):
        at = at or timezone.now()
        return self.filter(starts_at__lte=at, ends_at__gt=at, revoked_at__isnull=True)


class Delegation(models.Model):
    """A role holder hands their approval role to an acting officer for a period."""

    delegator = models.ForeignKey(User, on_delete=models.PROTECT, related_name="delegations_given")
    delegate = models.ForeignKey(User, on_delete=models.PROTECT, related_name="delegations_received")
    role = models.CharField(max_length=30, choices=[(r.value, r.label) for r in Role if r in DELEGABLE_ROLES])
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    reason = models.CharField(max_length=255, help_text="For example: annual leave, conference travel.")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    objects = DelegationQuerySet.as_manager()

    class Meta:
        ordering = ["-starts_at"]

    def __str__(self):
        return f"{self.delegate} acting as {self.get_role_display()} for {self.delegator}"

    def clean(self):
        errors = {}
        if self.delegator_id and self.delegate_id and self.delegator_id == self.delegate_id:
            errors["delegate"] = "Choose someone other than the role holder."
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            errors["ends_at"] = "The end must be after the start."
        if self.delegator_id and self.role and self.role not in self.delegator.direct_roles:
            errors["role"] = f"{self.delegator} does not hold the {self.get_role_display()} role."
        if self.delegate_id and not self.delegate.is_active:
            errors["delegate"] = "This account is inactive."
        if errors:
            raise ValidationError(errors)

    @property
    def status(self):
        now = timezone.now()
        if self.revoked_at:
            return "revoked"
        if self.ends_at <= now:
            return "ended"
        if self.starts_at > now:
            return "scheduled"
        return "active"


class LoginAttempt(models.Model):
    """Sign-in history. Failures drive the lockout in accounts.throttle."""

    identifier = models.CharField(max_length=254, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    successful = models.BooleanField(default=False)
    kind = models.CharField(max_length=12, default="login", help_text="login or signature")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        outcome = "success" if self.successful else "failure"
        return f"{self.identifier} {self.kind} {outcome} at {self.created_at:%Y-%m-%d %H:%M}"
