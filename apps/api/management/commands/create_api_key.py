from django.core.management.base import BaseCommand

from apps.api.models import ApiKey


class Command(BaseCommand):
    help = "Create a read-only API key for another CUT system. The key is shown once."

    def add_arguments(self, parser):
        parser.add_argument("name", help="Which system will use the key, for example 'ERP integration'.")

    def handle(self, *args, name, **options):
        key, raw = ApiKey.issue(name)
        self.stdout.write(self.style.SUCCESS(f"Created key {key.prefix} for {name}."))
        self.stdout.write("Store it now. It cannot be shown again:")
        self.stdout.write(raw)
