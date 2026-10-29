"""Daily reminders for requests past their stage target and for due
procurement follow-ups (CUT ICT 38 §5.9). Run on weekday mornings."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from apps.core.notify import notify
from apps.core.workdays import is_working_day, working_days_between
from apps.requisitions import workflow
from apps.requisitions.models import OPEN_STATES, Requisition, State


class Command(BaseCommand):
    help = "Remind officers about overdue requests and due procurement follow-ups."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Run even on a weekend or public holiday.")
        parser.add_argument("--dry-run", action="store_true", help="List what would be sent without sending.")

    def handle(self, *args, force=False, dry_run=False, **options):
        today = timezone.localdate()
        if not force and not is_working_day(today):
            self.stdout.write("Not a working day. No reminders sent.")
            return
        now = timezone.now()
        cutoff = now - timedelta(hours=20)  # at most one reminder a day per request
        sent = 0
        overdue = (
            Requisition.objects.active()
            .filter(state__in=list(OPEN_STATES), state_due_at__lt=now)
            .exclude(last_reminder_at__gte=cutoff)
            .select_related("requester", "assigned_technician")
        )
        for req in overdue:
            people = workflow.current_actors(req)
            if not people:
                continue
            late = working_days_between(req.state_due_at, now)
            title = f"Reminder: {req.reference} is waiting for you ({State(req.state).label})"
            body = (
                f"{req.title}\nRequested by {req.requester.display_name}. "
                f"It reached this stage on {timezone.localtime(req.state_entered_at):%d %b %Y} "
                f"and is {late} working day{'s' if late != 1 else ''} past the target."
            )
            sent += self._send(req, people, title, body, dry_run)

        follow_ups = (
            Requisition.objects.active()
            .filter(state=State.WITH_PROCUREMENT, next_follow_up_on__lte=today, assigned_technician__isnull=False)
            .exclude(last_reminder_at__gte=cutoff)
            .select_related("assigned_technician")
        )
        for req in follow_ups:
            title = f"Follow up {req.reference} with Procurement today"
            body = (
                f"{req.title}\nProcurement reference: {req.procurement_reference or 'not recorded'}. "
                "Log the follow-up on the request and set the next date."
            )
            sent += self._send(req, [req.assigned_technician], title, body, dry_run)
        verb = "Would send" if dry_run else "Sent"
        self.stdout.write(self.style.SUCCESS(f"{verb} {sent} reminder(s)."))

    def _send(self, req, people, title, body, dry_run):
        if dry_run:
            self.stdout.write(f"{req.reference}: {', '.join(p.email for p in people)}")
            return len(people)
        count = notify(people, title, body, reverse("requisitions:detail", args=[req.public_id]))
        Requisition.objects.filter(pk=req.pk).update(last_reminder_at=timezone.now())
        return count
