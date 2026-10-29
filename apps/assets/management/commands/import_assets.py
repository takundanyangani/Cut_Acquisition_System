"""Load an existing asset register from CSV (for example an export from the
current asset management system named in CUT ICT 40 §5.2).

Columns (header row required; only the first four are mandatory):
  asset_tag, category, make, model, serial_number, specification, condition,
  status, holder_email, campus, building, office, supplier, purchase_order,
  acquired_on, warranty_expires_on, notes

category and campus accept a code or a name. status is in_stock (default)
or allocated, which needs holder_email. Dates are YYYY-MM-DD.
Runs as a dry run unless --commit is given; on --commit every row is saved
or none are.
"""

import csv
from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from apps.assets.models import (
    Asset,
    AssetCategory,
    AssetMovement,
    AssetStatus,
    Condition,
    MovementKind,
    normalise_serial,
)
from apps.core.models import Campus

REQUIRED = ["asset_tag", "category", "make", "model"]


class RowError(Exception):
    pass


def _lookup(model, value, label):
    value = (value or "").strip()
    if not value:
        return None
    obj = model.objects.filter(Q(code__iexact=value) | Q(name__iexact=value)).first()
    if obj is None:
        raise RowError(f"unknown {label} '{value}'")
    return obj


def _date(value, label):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RowError(f"{label} must be YYYY-MM-DD, got '{value}'") from exc


class Command(BaseCommand):
    help = "Import assets from a CSV file. Dry run unless --commit."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--commit", action="store_true", help="Save the rows. Without it, only validate.")

    def handle(self, *args, path, commit=False, **options):
        User = get_user_model()
        try:
            handle = open(path, newline="", encoding="utf-8-sig")
        except OSError as exc:
            raise CommandError(f"Cannot open {path}: {exc}") from exc
        with handle:
            reader = csv.DictReader(handle)
            missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
            if missing:
                raise CommandError(f"Missing column(s): {', '.join(missing)}")
            rows, errors = [], []
            tags, serials = set(), set()
            for line, row in enumerate(reader, start=2):
                try:
                    rows.append(self._parse(row, User, tags, serials))
                except RowError as exc:
                    errors.append(f"line {line}: {exc}")
        for error in errors:
            self.stdout.write(self.style.ERROR(error))
        if errors:
            raise CommandError(f"{len(errors)} row(s) have problems. Nothing was imported.")
        if not commit:
            self.stdout.write(self.style.SUCCESS(f"{len(rows)} row(s) are valid. Run again with --commit to import."))
            return
        with transaction.atomic():
            for fields in rows:
                asset = Asset.objects.create(**fields)
                AssetMovement.objects.create(
                    asset=asset,
                    kind=MovementKind.IMPORTED,
                    to_status=asset.status,
                    to_holder=asset.holder,
                    location_text=asset.location_text,
                    note=f"Imported from {path}",
                )
        self.stdout.write(self.style.SUCCESS(f"Imported {len(rows)} asset(s)."))

    def _parse(self, row, User, tags, serials):
        for column in REQUIRED:
            if not (row.get(column) or "").strip():
                raise RowError(f"{column} is empty")
        tag = row["asset_tag"].strip().upper()
        if tag in tags or Asset.objects.filter(asset_tag=tag).exists():
            raise RowError(f"asset tag {tag} is already in use")
        tags.add(tag)
        category = _lookup(AssetCategory, row["category"], "category")
        serial = normalise_serial(row.get("serial_number"))
        if category.requires_serial and not serial:
            raise RowError(f"{category} items need a serial number")
        if serial:
            if serial in serials or Asset.objects.filter(serial_number=serial).exists():
                raise RowError(f"serial number {serial} is already in use")
            serials.add(serial)
        condition = (row.get("condition") or Condition.GOOD).strip().lower()
        if condition not in Condition.values:
            raise RowError(f"condition must be one of {', '.join(Condition.values)}")
        status = (row.get("status") or AssetStatus.IN_STOCK).strip().lower()
        if status not in {AssetStatus.IN_STOCK, AssetStatus.ALLOCATED}:
            raise RowError("status must be in_stock or allocated")
        holder = None
        email = (row.get("holder_email") or "").strip()
        if status == AssetStatus.ALLOCATED:
            if not email:
                raise RowError("allocated assets need holder_email")
            holder = User.objects.filter(email__iexact=email).first()
            if holder is None:
                raise RowError(f"no user with email {email}; import users first")
        return {
            "asset_tag": tag,
            "serial_number": serial,
            "category": category,
            "make": row["make"].strip(),
            "model": row["model"].strip(),
            "specification": (row.get("specification") or "").strip() or "Not recorded at import",
            "condition": condition,
            "status": status,
            "holder": holder,
            "campus": _lookup(Campus, row.get("campus"), "campus"),
            "building": (row.get("building") or "").strip(),
            "office": (row.get("office") or "").strip(),
            "supplier": (row.get("supplier") or "").strip(),
            "purchase_order_ref": (row.get("purchase_order") or "").strip(),
            "acquired_on": _date(row.get("acquired_on"), "acquired_on"),
            "warranty_expires_on": _date(row.get("warranty_expires_on"), "warranty_expires_on"),
            "notes": (row.get("notes") or "").strip(),
        }
