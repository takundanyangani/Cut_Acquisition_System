from django.core.management.base import BaseCommand, CommandError

from apps.requisitions.models import Requisition, verify_chain


class Command(BaseCommand):
    help = "Check the hash chain of every request's audit trail. Exit status 1 if any chain is broken."

    def add_arguments(self, parser):
        parser.add_argument("references", nargs="*", help="Check only these references.")

    def handle(self, *args, references=None, **options):
        queryset = Requisition.objects.exclude(reference__isnull=True).order_by("reference")
        if references:
            queryset = queryset.filter(reference__in=references)
            missing = set(references) - set(queryset.values_list("reference", flat=True))
            if missing:
                raise CommandError(f"Unknown reference(s): {', '.join(sorted(missing))}")
        broken = 0
        checked = 0
        for req in queryset.iterator():
            checked += 1
            problems = verify_chain(req)
            if problems:
                broken += 1
                for problem in problems:
                    self.stdout.write(self.style.ERROR(f"{req.reference}: {problem}"))
        if broken:
            raise CommandError(f"{broken} of {checked} audit trail(s) failed verification.")
        self.stdout.write(self.style.SUCCESS(f"All {checked} audit trail(s) are intact."))
