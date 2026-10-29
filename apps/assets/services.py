"""Every change to an asset's custody or status goes through these functions,
so the movement history (CUT ICT 40 KPI 7.1, CUT ICT 42 KPI 7.1) stays complete."""

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import next_reference

from .models import (
    MANUAL_STATUS_CHANGES,
    Asset,
    AssetMovement,
    AssetStatus,
    IssueVoucher,
    IssueVoucherLine,
    MovementKind,
    normalise_serial,
)


class AssetError(Exception):
    """A rule about assets was broken. The message is shown to the user."""


def generate_asset_tag():
    return next_reference("ICT", width=5)


def register_asset(*, performed_by, kind=MovementKind.REGISTERED, requisition=None, note="", **fields):
    """Create an asset in stock and log how it arrived."""
    if not fields.get("asset_tag"):
        fields["asset_tag"] = generate_asset_tag()
    fields["serial_number"] = normalise_serial(fields.get("serial_number"))
    if fields["serial_number"] and Asset.objects.filter(serial_number=fields["serial_number"]).exists():
        raise AssetError(f"Serial number {fields['serial_number']} is already in the register.")
    if Asset.objects.filter(asset_tag=fields["asset_tag"].strip().upper()).exists():
        raise AssetError(f"Asset tag {fields['asset_tag']} is already in use.")
    fields.setdefault("status", AssetStatus.IN_STOCK)
    try:
        with transaction.atomic():
            asset = Asset.objects.create(
                created_by=performed_by, source_requisition=requisition, created_at=timezone.now(), **fields
            )
    except IntegrityError as exc:
        raise AssetError("That asset tag or serial number was registered by someone else a moment ago.") from exc
    AssetMovement.objects.create(
        asset=asset,
        kind=kind,
        to_status=asset.status,
        to_holder=asset.holder,
        location_text=asset.location_text,
        requisition=requisition,
        performed_by=performed_by,
        note=note,
        created_at=timezone.now(),
    )
    return asset


def _move(
    asset,
    *,
    kind,
    to_status,
    performed_by,
    to_holder=None,
    keep_holder=False,
    voucher=None,
    requisition=None,
    note="",
    location=None,
):
    movement = AssetMovement(
        asset=asset,
        kind=kind,
        from_status=asset.status,
        to_status=to_status,
        from_holder=asset.holder,
        voucher=voucher,
        requisition=requisition,
        performed_by=performed_by,
        note=note,
        created_at=timezone.now(),
    )
    asset.status = to_status
    if not keep_holder:
        asset.holder = to_holder
    if location is not None:
        asset.campus, asset.building, asset.office = location
    movement.to_holder = asset.holder
    movement.location_text = asset.location_text
    asset.save()
    movement.save()
    return movement


def change_status(asset, to_status, *, performed_by, note):
    """A status change recorded by hand from the asset page."""
    from apps.accounts.models import Role

    with transaction.atomic():
        asset = Asset.objects.select_for_update().get(pk=asset.pk)
        allowed = MANUAL_STATUS_CHANGES.get(AssetStatus(asset.status), set())
        if to_status not in allowed:
            raise AssetError(
                f"An asset that is {asset.get_status_display().lower()} cannot be marked "
                f"{AssetStatus(to_status).label.lower()} by hand."
            )
        if to_status == AssetStatus.DISPOSED and not performed_by.has_any_role(
            {Role.CLIENT_SERVICES_MANAGER, Role.ICT_DIRECTOR}
        ):
            raise AssetError("Only the Client Services Manager or the ICT Director can record a disposal.")
        if not note.strip():
            raise AssetError("Give a reason for the change.")
        kind = MovementKind.RETURNED if to_status == AssetStatus.RETURNED else MovementKind.STATUS_CHANGED
        keep_holder = to_status == AssetStatus.LOST and asset.status == AssetStatus.ALLOCATED
        return _move(
            asset, kind=kind, to_status=to_status, performed_by=performed_by, keep_holder=keep_holder, note=note
        )


def lock_available(asset_ids):
    """Lock the chosen assets and confirm each is still in stock."""
    assets = list(Asset.objects.select_for_update().filter(pk__in=asset_ids).select_related("category"))
    if len(assets) != len(set(asset_ids)):
        raise AssetError("One of the chosen assets no longer exists.")
    taken = [a.asset_tag for a in assets if a.status != AssetStatus.IN_STOCK]
    if taken:
        raise AssetError(
            "These assets are no longer in stock: " + ", ".join(taken) + ". Refresh the page and choose again."
        )
    return assets


def raise_voucher(
    *,
    requisition,
    assets,
    issued_by,
    campus,
    building,
    office,
    notes="",
    replaced_asset=None,
    replaced_collected=None,
    replaced_condition="",
    replaced_note="",
):
    voucher = IssueVoucher.objects.create(
        number=next_reference("IV"),
        requisition=requisition,
        issued_to=requisition.requester,
        issued_by=issued_by,
        campus=campus,
        building=building,
        office=office,
        notes=notes,
        replaced_asset=replaced_asset,
        replaced_asset_collected=replaced_collected,
        replaced_asset_condition=replaced_condition or "",
        replaced_asset_note=replaced_note,
        created_at=timezone.now(),
    )
    for asset in assets:
        IssueVoucherLine.objects.create(
            voucher=voucher,
            asset=asset,
            asset_tag=asset.asset_tag,
            serial_number=asset.serial_number or "",
            category_name=asset.category.name,
            make_model=asset.make_model,
            specification=asset.specification,
            condition=asset.condition,
        )
        _move(
            asset,
            kind=MovementKind.RESERVED,
            to_status=AssetStatus.RESERVED,
            performed_by=issued_by,
            voucher=voucher,
            requisition=requisition,
            note=f"On voucher {voucher.number}",
        )
    if replaced_asset is not None and replaced_collected:
        old = Asset.objects.select_for_update().get(pk=replaced_asset.pk)
        if old.status == AssetStatus.ALLOCATED:
            if replaced_condition:
                old.condition = replaced_condition
            _move(
                old,
                kind=MovementKind.RETURNED,
                to_status=AssetStatus.RETURNED,
                performed_by=issued_by,
                voucher=voucher,
                requisition=requisition,
                note=replaced_note or f"Collected when {voucher.number} was issued",
            )
    return voucher


def _release(voucher, performed_by, note):
    for line in voucher.lines.select_related("asset"):
        asset = Asset.objects.select_for_update().get(pk=line.asset_id)
        if asset.status == AssetStatus.RESERVED:
            _move(
                asset,
                kind=MovementKind.RELEASED,
                to_status=AssetStatus.IN_STOCK,
                performed_by=performed_by,
                voucher=voucher,
                requisition=voucher.requisition,
                note=note,
            )


def acknowledge_voucher(voucher, *, user, ip, statement):
    now = timezone.now()
    for line in voucher.lines.select_related("asset"):
        asset = Asset.objects.select_for_update().get(pk=line.asset_id)
        if asset.status != AssetStatus.RESERVED:
            raise AssetError(f"{asset.asset_tag} is not on hold for this voucher. Ask the Helpdesk to check it.")
        _move(
            asset,
            kind=MovementKind.ISSUED,
            to_status=AssetStatus.ALLOCATED,
            performed_by=user,
            to_holder=voucher.issued_to,
            voucher=voucher,
            requisition=voucher.requisition,
            location=(voucher.campus, voucher.building, voucher.office),
            note=f"Signed for on {voucher.number}",
        )
    voucher.status = IssueVoucher.Status.ACKNOWLEDGED
    voucher.acknowledged_at = now
    voucher.acknowledgement_text = statement
    voucher.acknowledged_ip = ip
    voucher.save()


def dispute_voucher(voucher, *, user, reason):
    _release(voucher, user, f"Recipient disputed {voucher.number}")
    voucher.status = IssueVoucher.Status.DISPUTED
    voucher.disputed_at = timezone.now()
    voucher.dispute_reason = reason
    voucher.save()


def withdraw_voucher(voucher, *, user, reason):
    _release(voucher, user, f"ICT withdrew {voucher.number}")
    voucher.status = IssueVoucher.Status.WITHDRAWN
    voucher.withdrawn_at = timezone.now()
    voucher.withdrawn_reason = reason
    voucher.save()
