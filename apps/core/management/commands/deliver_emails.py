from django.core.management.base import BaseCommand

from apps.core.notify import deliver


class Command(BaseCommand):
    help = "Send queued emails from the outbox, retrying earlier failures. Run every few minutes."

    def handle(self, *args, **options):
        sent = deliver()
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} email(s)."))
