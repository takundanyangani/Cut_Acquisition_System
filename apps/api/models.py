import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


def hash_key(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ApiKey(models.Model):
    """A read-only key for another CUT system. Only a hash is stored; the
    key itself is shown once, when it is created."""

    name = models.CharField(max_length=120, help_text="Which system uses this key, for example the ERP.")
    prefix = models.CharField(max_length=12, unique=True)
    key_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "API key"

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"

    @property
    def is_active(self):
        return self.revoked_at is None

    @classmethod
    def issue(cls, name, created_by=None):
        prefix = secrets.token_hex(4)
        raw = f"cutacq_{prefix}_{secrets.token_urlsafe(32)}"
        key = cls.objects.create(name=name, prefix=prefix, key_hash=hash_key(raw), created_by=created_by)
        return key, raw

    @classmethod
    def authenticate(cls, raw):
        if not raw or not raw.startswith("cutacq_"):
            return None
        key = cls.objects.filter(key_hash=hash_key(raw), revoked_at__isnull=True).first()
        if key is not None:
            cls.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
        return key
