"""Move closed records to the archive once their retention period ends.

Retention follows the Retained Documented Information tables in the SOPs:
CUT ICT 38 one semester, CUT ICT 40 and 42 one year, then "Archives".
Nothing is deleted. Also prunes old sign-in attempts and sent emails.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import LoginAttempt
from apps.core.models import OutboundEmail
from apps.requisitions.models import TERMINAL_STATES, Requisition


class Command(BaseCommand):
    help = "Archive closed requests past their retention period and prune housekeeping tables."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, dry_run=False, **options):
        now = timezone.now()
        total = 0
        for kind, days in settings.ACQ_RETENTION_DAYS.items():
            due = Requisition.objects.filter(
                kind=kind,
                state__in=list(TERMINAL_STATES),
                archived_at__isnull=True,
                closed_at__lt=now - timedelta(days=days),
            )
            count = due.count()
            if not dry_run and count:
                due.update(archived_at=now)
            total += count
            self.stdout.write(f"{kind}: {count} request(s) {'would move' if dry_run else 'moved'} to the archive")
        if not dry_run:
            pruned_logins = LoginAttempt.objects.filter(created_at__lt=now - timedelta(days=365)).delete()[0]
            pruned_mail = OutboundEmail.objects.filter(
                status=OutboundEmail.Status.SENT, sent_at__lt=now - timedelta(days=90)
            ).delete()[0]
            self.stdout.write(f"Pruned {pruned_logins} sign-in record(s) and {pruned_mail} sent email(s).")
        self.stdout.write(self.style.SUCCESS(f"Done. {total} request(s) archived."))
