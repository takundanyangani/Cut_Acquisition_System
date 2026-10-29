"""In-app notifications and the email outbox."""

import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import close_old_connections, connections, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from .models import Notification, OutboundEmail

log = logging.getLogger(__name__)


def absolute_url(path):
    if path.startswith(("http://", "https://")):
        return path
    return f"{settings.ACQ_SITE_URL}{path}"


def notify(users, title, body="", link="", *, email=True):
    """Create one in-app notification per user and queue matching emails.

    `users` may contain duplicates or None; inactive users are skipped.
    """
    seen = set()
    email_ids = []
    for user in users:
        if user is None or not user.is_active or user.pk in seen:
            continue
        seen.add(user.pk)
        Notification.objects.create(recipient=user, title=title[:200], body=body, link=link, created_at=timezone.now())
        if email and user.email:
            message = render_to_string(
                "emails/notification.txt",
                {
                    "user": user,
                    "title": title,
                    "body": body,
                    "url": absolute_url(link) if link else settings.ACQ_SITE_URL,
                    "site_url": settings.ACQ_SITE_URL,
                    "unit_name": settings.ACQ_UNIT_NAME,
                    "org_name": settings.ACQ_ORG_NAME,
                },
            )
            outbound = OutboundEmail.objects.create(
                to=user.email, subject=title[:250], body=message, created_at=timezone.now()
            )
            email_ids.append(outbound.pk)
    if email_ids:
        transaction.on_commit(lambda: dispatch(email_ids))
    return len(seen)


def dispatch(email_ids):
    mode = settings.ACQ_EMAIL_DELIVERY
    if mode == "cron":
        return
    if mode == "inline":
        deliver(email_ids)
        return
    threading.Thread(target=_deliver_in_thread, args=(list(email_ids),), daemon=True).start()


def _deliver_in_thread(email_ids):
    close_old_connections()
    try:
        deliver(email_ids)
    except Exception:  # pragma: no cover - logged, cron retries
        log.exception("Background email delivery failed")
    finally:
        connections.close_all()


def deliver(email_ids=None, limit=200):
    """Send pending messages. Safe to run from several processes at once:
    each message is claimed with a conditional UPDATE before sending."""
    stale = timezone.now() - timedelta(minutes=10)
    OutboundEmail.objects.filter(status=OutboundEmail.Status.SENDING, claimed_at__lt=stale).update(
        status=OutboundEmail.Status.PENDING
    )
    queryset = OutboundEmail.objects.filter(
        status=OutboundEmail.Status.PENDING, attempts__lt=OutboundEmail.MAX_ATTEMPTS
    )
    if email_ids is not None:
        queryset = queryset.filter(pk__in=email_ids)
    sent = 0
    for outbound in queryset.order_by("created_at")[:limit]:
        claimed = OutboundEmail.objects.filter(pk=outbound.pk, status=OutboundEmail.Status.PENDING).update(
            status=OutboundEmail.Status.SENDING, claimed_at=timezone.now()
        )
        if not claimed:
            continue
        attempts = outbound.attempts + 1
        try:
            EmailMessage(
                subject=outbound.subject,
                body=outbound.body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[outbound.to],
            ).send(fail_silently=False)
        except Exception as exc:  # SMTP errors vary by server; keep the message for retry
            status = (
                OutboundEmail.Status.FAILED if attempts >= OutboundEmail.MAX_ATTEMPTS else OutboundEmail.Status.PENDING
            )
            OutboundEmail.objects.filter(pk=outbound.pk).update(
                status=status, attempts=attempts, last_error=str(exc)[:2000]
            )
            log.warning("Email to %s failed (attempt %s): %s", outbound.to, attempts, exc)
        else:
            OutboundEmail.objects.filter(pk=outbound.pk).update(
                status=OutboundEmail.Status.SENT, attempts=attempts, sent_at=timezone.now(), last_error=""
            )
            sent += 1
    return sent
