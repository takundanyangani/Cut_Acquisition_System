from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.utils import timezone


class Campus(models.Model):
    name = models.CharField(max_length=120, unique=True)
    code = models.CharField(max_length=12, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "campuses"

    def __str__(self):
        return self.name


class Department(models.Model):
    name = models.CharField(max_length=160, unique=True)
    code = models.CharField(max_length=12, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Holiday(models.Model):
    """Public holidays, skipped when working-day targets are calculated."""

    date = models.DateField(unique=True)
    name = models.CharField(max_length=120)

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"{self.name} ({self.date:%d %b %Y})"


class Sequence(models.Model):
    """Gap-free counters for reference numbers, one row per prefix and year."""

    key = models.CharField(max_length=10)
    year = models.PositiveSmallIntegerField()
    value = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["key", "year"], name="unique_sequence_per_year")]

    def __str__(self):
        return f"{self.key}-{self.year}: {self.value}"

    @classmethod
    def next_value(cls, key, year):
        """Return the next number. Must run inside a transaction."""
        for _ in range(3):
            try:
                with transaction.atomic():
                    seq, _ = cls.objects.select_for_update().get_or_create(key=key, year=year)
                    seq.value += 1
                    seq.save(update_fields=["value"])
                    return seq.value
            except IntegrityError:
                # Another transaction created the row first. Retry and lock it.
                continue
        raise RuntimeError(f"Could not allocate a number for {key}-{year}")


def next_reference(prefix, width=4):
    year = timezone.localdate().year
    return f"{prefix}-{year}-{Sequence.next_value(prefix, year):0{width}d}"


class Notification(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    link = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "read_at"])]

    def __str__(self):
        return self.title

    @property
    def is_unread(self):
        return self.read_at is None


class OutboundEmail(models.Model):
    """Email outbox. Messages are written here first and sent after commit,
    so a slow or broken mail server never blocks or loses an approval."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    to = models.EmailField()
    subject = models.CharField(max_length=250)
    body = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    claimed_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    MAX_ATTEMPTS = 6

    class Meta:
        ordering = ["created_at"]
        verbose_name = "outbound email"

    def __str__(self):
        return f"{self.subject} to {self.to}"
