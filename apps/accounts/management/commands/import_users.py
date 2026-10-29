"""Create or update staff accounts from a CSV export (HR or the directory).

Columns: email, first_name, last_name, and optionally title, staff_number,
job_title, department, campus, building, office, phone_extension, roles.
department and campus accept a code or a name. roles is a semicolon list of
role codes, for example "technician;csm". Valid codes: personal_advisor,
ict_director, csm, technician, operations_manager, procurement_officer,
auditor, system_admin.

New accounts get a random password nobody knows. Staff then sign in through
LDAP, if configured, or set a password with "Forgotten your password?".
Dry run unless --commit.
"""

import csv
import secrets

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q

from apps.accounts.models import Role, RoleGrant, User
from apps.core.models import Campus, Department

REQUIRED = ["email", "first_name", "last_name"]
OPTIONAL = ["title", "staff_number", "job_title", "building", "office", "phone_extension"]


class Command(BaseCommand):
    help = "Import staff accounts and roles from CSV. Dry run unless --commit."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--commit", action="store_true")
        parser.add_argument(
            "--replace-roles", action="store_true", help="Remove roles not listed in the file for each user."
        )

    def handle(self, *args, path, commit=False, replace_roles=False, **options):
        try:
            handle = open(path, newline="", encoding="utf-8-sig")
        except OSError as exc:
            raise CommandError(f"Cannot open {path}: {exc}") from exc
        parsed, errors = [], []
        with handle:
            reader = csv.DictReader(handle)
            missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
            if missing:
                raise CommandError(f"Missing column(s): {', '.join(missing)}")
            seen = set()
            for line, row in enumerate(reader, start=2):
                try:
                    parsed.append(self._parse(row, seen))
                except ValueError as exc:
                    errors.append(f"line {line}: {exc}")
        for error in errors:
            self.stdout.write(self.style.ERROR(error))
        if errors:
            raise CommandError(f"{len(errors)} row(s) have problems. Nothing was imported.")
        created = updated = 0
        if commit:
            with transaction.atomic():
                for fields, roles in parsed:
                    email = fields.pop("email")
                    user = User.objects.filter(email__iexact=email).first()
                    if user is None:
                        user = User(email=email)
                        user.set_password(secrets.token_urlsafe(24))
                        created += 1
                    else:
                        updated += 1
                    for key, value in fields.items():
                        setattr(user, key, value)
                    user.save()
                    if replace_roles:
                        user.role_grants.exclude(role__in=roles).delete()
                    for role in roles:
                        RoleGrant.objects.get_or_create(user=user, role=role)
            self.stdout.write(self.style.SUCCESS(f"Created {created} and updated {updated} account(s)."))
        else:
            self.stdout.write(self.style.SUCCESS(f"{len(parsed)} row(s) are valid. Run again with --commit."))

    def _parse(self, row, seen):
        email = (row.get("email") or "").strip().lower()
        try:
            validate_email(email)
        except ValidationError as exc:
            raise ValueError(f"'{email}' is not a valid email address") from exc
        if email in seen:
            raise ValueError(f"{email} appears twice")
        seen.add(email)
        fields = {"email": email}
        for column in REQUIRED[1:]:
            value = (row.get(column) or "").strip()
            if not value:
                raise ValueError(f"{column} is empty")
            fields[column] = value
        for column in OPTIONAL:
            if column in row:
                fields[column] = (row.get(column) or "").strip()
        if "staff_number" in fields:
            fields["staff_number"] = fields["staff_number"] or None
            if (
                fields["staff_number"]
                and User.objects.filter(staff_number__iexact=fields["staff_number"])
                .exclude(email__iexact=email)
                .exists()
            ):
                raise ValueError(f"staff number {fields['staff_number']} belongs to another account")
        for column, model in (("department", Department), ("campus", Campus)):
            value = (row.get(column) or "").strip()
            if value:
                obj = model.objects.filter(Q(code__iexact=value) | Q(name__iexact=value)).first()
                if obj is None:
                    raise ValueError(f"unknown {column} '{value}'")
                fields[column] = obj
        roles = [r.strip() for r in (row.get("roles") or "").split(";") if r.strip()]
        bad = [r for r in roles if r not in Role.values]
        if bad:
            raise ValueError(f"unknown role(s): {', '.join(bad)}")
        return fields, roles
