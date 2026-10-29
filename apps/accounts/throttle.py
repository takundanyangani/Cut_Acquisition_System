"""Account lockout after repeated failed sign-ins or signatures.

Attempts live in the database so the limit holds across every Gunicorn
worker and server.
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import Max
from django.utils import timezone

from .models import LoginAttempt


def client_ip(request):
    if request is None:
        return None
    if settings.ACQ_TRUST_X_FORWARDED_FOR:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def _window_start():
    return timezone.now() - timedelta(minutes=settings.ACQ_LOGIN_LOCK_MINUTES)


def normalise(identifier):
    return (identifier or "").strip().lower()[:254]


def is_locked(identifier, ip=None):
    since = _window_start()
    identifier = normalise(identifier)
    if identifier:
        last_success = LoginAttempt.objects.filter(
            identifier=identifier, successful=True, created_at__gte=since
        ).aggregate(t=Max("created_at"))["t"]
        failures = LoginAttempt.objects.filter(
            identifier=identifier, successful=False, created_at__gte=last_success or since
        ).count()
        if failures >= settings.ACQ_LOGIN_MAX_FAILURES:
            return True
    if ip:
        ip_failures = LoginAttempt.objects.filter(ip_address=ip, successful=False, created_at__gte=since).count()
        if ip_failures >= settings.ACQ_LOGIN_MAX_FAILURES_PER_IP:
            return True
    return False


def record(identifier, ip, *, successful, kind="login"):
    LoginAttempt.objects.create(identifier=normalise(identifier), ip_address=ip, successful=successful, kind=kind)


def lock_message():
    minutes = settings.ACQ_LOGIN_LOCK_MINUTES
    return (
        f"Too many failed attempts. Wait {minutes} minutes and try again, or contact {settings.ACQ_HELPDESK_CONTACT}."
    )
