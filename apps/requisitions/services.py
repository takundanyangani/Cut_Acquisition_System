"""
The workflow engine. perform() is the only code that changes a request's
stage. It locks the row, checks the version the user saw, checks who may
act, verifies the signature, runs the step's handler, writes the audit
entry and queues notifications, all in one transaction.
"""

import hashlib
import logging
import mimetypes
import os
from dataclasses import dataclass, field

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.accounts.forms import verify_signature
from apps.accounts.models import Role
from apps.accounts.throttle import client_ip
from apps.assets import services as asset_services
from apps.assets.models import AssetStatus, IssueVoucher
from apps.core.models import next_reference
from apps.core.notify import notify
from apps.core.workdays import add_working_days

from . import workflow
from .models import (
    TERMINAL_STATES,
    Attachment,
    AuditEntry,
    Kind,
    Requisition,
    RequisitionItem,
    Route,
    State,
)

log = logging.getLogger(__name__)


class WorkflowError(Exception):
    """A business rule stopped the action. The message is shown to the user."""


class StaleRequisition(WorkflowError):
    pass


class SignatureError(WorkflowError):
    pass


@dataclass
class Outcome:
    extra: dict = field(default_factory=dict)
    target: str | None = None
    notify_users: list = field(default_factory=list)
    notify_title: str = ""


# -- Audit trail -------------------------------------------------------------


def record(
    requisition,
    *,
    action,
    label,
    actor,
    capacity=None,
    from_state="",
    to_state="",
    comment="",
    signed=False,
    sop="",
    data=None,
    request=None,
):
    """Append an entry to the request's hash-chained audit trail."""
    last = requisition.audit_entries.order_by("-seq").first()
    entry = AuditEntry(
        requisition=requisition,
        seq=(last.seq + 1) if last else 1,
        action=action,
        label=label,
        actor=actor,
        actor_name=actor.display_name if actor else "System",
        actor_role=capacity.role if capacity else "",
        actor_role_label=capacity.role_label if capacity else "",
        on_behalf_of=capacity.on_behalf_of if capacity else None,
        on_behalf_of_name=capacity.on_behalf_of.display_name if capacity and capacity.on_behalf_of else "",
        from_state=from_state or "",
        to_state=to_state or "",
        comment=comment or "",
        signed=signed,
        sop_clause=sop,
        data={k: ("" if v is None else str(v)) for k, v in (data or {}).items()},
        ip_address=client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:255] if request else ""),
        created_at=timezone.now(),
        prev_hash=last.entry_hash if last else AuditEntry.GENESIS,
    )
    entry.entry_hash = entry.compute_hash()
    entry.save()
    return entry


# -- Stage changes -----------------------------------------------------------


def _enter_state(requisition, target, from_state):
    now = timezone.now()
    requisition.state = target
    requisition.state_entered_at = now
    days = settings.ACQ_STAGE_TARGET_DAYS.get(target)
    requisition.state_due_at = add_working_days(now, days) if days else None
    requisition.last_reminder_at = None
    if target == State.RETURNED:
        requisition.returned_from_state = from_state
    elif from_state == State.RETURNED:
        requisition.returned_from_state = ""
    if target in TERMINAL_STATES:
        requisition.closed_at = now


def perform(requisition, user, code, *, data=None, expected_version=None, request=None, password=None):
    """Carry out workflow step `code` on `requisition` as `user`."""
    data = data or {}
    current = Requisition.objects.get(pk=requisition.pk)
    _check_version(current, expected_version)
    first_look, _ = _check_allowed(current, user, code)
    if first_look.sign and settings.ACQ_SIGNATURE_REQUIRES_PASSWORD:
        # Outside the transaction, so a failed attempt is kept and counts
        # toward the account lockout even though the action is refused.
        try:
            verify_signature(request, user, password)
        except ValidationError as exc:
            raise SignatureError(exc.messages[0]) from exc
    with transaction.atomic():
        req = Requisition.objects.select_for_update().get(pk=requisition.pk)
        _check_version(req, expected_version)
        transition, cap = _check_allowed(req, user, code)
        comment = (data.get("comment") or "").strip()
        if transition.comment_required and not comment:
            raise WorkflowError("A comment is required for this action.")

        from_state = req.state
        handler = HANDLERS.get(transition.code)
        outcome = (handler(req, user, data, transition, request) if handler else None) or Outcome()
        target = outcome.target or transition.target
        signed = transition.sign and settings.ACQ_SIGNATURE_REQUIRES_PASSWORD
        if signed:
            outcome.extra["statement"] = workflow.signing_statement(req, user, transition, cap)
        if target and target != from_state:
            _enter_state(req, target, from_state)
        req.version += 1
        req.save()
        entry = record(
            req,
            action=transition.code,
            label=transition.done,
            actor=user,
            capacity=cap,
            from_state=from_state,
            to_state=req.state,
            comment=comment,
            signed=signed,
            sop=transition.sop,
            data=outcome.extra,
            request=request,
        )
        _notify_after(req, user, transition, entry, outcome)
    return req


def _check_version(req, expected_version):
    if expected_version is not None and req.version != int(expected_version):
        raise StaleRequisition(
            "Someone else updated this request while you had it open. Review the latest version below and try again."
        )


def _check_allowed(req, user, code):
    transition = workflow.find_transition(req, code)
    if transition is None:
        raise WorkflowError("That action is not available at this stage.")
    cap = workflow.capacity(req, user, transition)
    if cap is None:
        raise WorkflowError("You are not allowed to take this action on this request.")
    return transition, cap


def _notify_after(req, actor, transition, entry, outcome):
    link = reverse("requisitions:detail", args=[req.public_id])
    ref = req.reference
    state_label = State(req.state).label
    notified = {actor.pk}
    if outcome.notify_users:
        targets = [p for p in outcome.notify_users if p and p.pk not in notified]
        notify(targets, outcome.notify_title or f"{ref}: {transition.done}", req.title, link)
        notified.update(p.pk for p in targets)
    if req.state != entry.from_state:
        actors = [p for p in workflow.current_actors(req) if p.pk not in notified and p.pk != req.requester_id]
        if actors:
            notify(
                actors,
                f"{ref} needs your action: {state_label}",
                f"{req.title}\nRequested by {req.requester.display_name}.\n{actor.display_name}: {transition.done}.",
                link,
            )
            notified.update(p.pk for p in actors)
    if req.requester_id not in notified:
        body = f"{actor.display_name} {transition.done[0].lower() + transition.done[1:]}."
        if entry.comment:
            body += f"\nComment: {entry.comment}"
        if req.state == State.AWAITING_ACK:
            title = f"{ref}: your equipment is ready. Sign the issue voucher"
        elif req.state == State.RETURNED:
            title = f"{ref} was returned to you for changes"
        else:
            title = f"{ref}: {state_label}"
        notify([req.requester], title, body, link)


# -- Handlers ----------------------------------------------------------------


def _validate_complete(req):
    if not req.items.exists():
        raise WorkflowError("Add at least one item before you submit.")
    if not req.campus_id or not req.building:
        raise WorkflowError("Give the campus and building where the equipment will be used.")
    if req.kind == Kind.REPLACEMENT:
        if not (req.asset_to_replace_id or req.replaced_item_description.strip()):
            raise WorkflowError("Say which machine you want replaced.")
        if not req.replacement_reason:
            raise WorkflowError("Choose the reason for replacement.")


def handle_submit(req, user, data, t, request):
    _validate_complete(req)
    resubmission = req.state == State.RETURNED
    if req.reference is None:
        req.reference = next_reference(req.kind)
        req.submitted_at = timezone.now()
    if resubmission and req.returned_from_state:
        target = req.returned_from_state
    else:
        target = workflow.FIRST_STAGE[Kind(req.kind)]
    return Outcome(target=target, extra={"resubmission": "yes" if resubmission else ""})


def _technician_from(data, req):
    technician = data.get("technician")
    if technician is None:
        raise WorkflowError("Choose a technician.")
    if not technician.is_active or not technician.has_role(Role.TECHNICIAN):
        raise WorkflowError(f"{technician.display_name} is not an active Help Desk Technician.")
    if technician.pk == req.requester_id:
        raise WorkflowError("The requester cannot be assigned to their own request.")
    return technician


def handle_assign(req, user, data, t, request):
    technician = _technician_from(data, req)
    previous = req.assigned_technician
    req.assigned_technician = technician
    outcome = Outcome(extra={"technician": technician.display_name})
    if previous and previous.pk != technician.pk:
        outcome.extra["previous_technician"] = previous.display_name
    if t.code in {"csm_assign", "reassign"}:
        outcome.notify_users = [technician]
        outcome.notify_title = f"{req.reference} has been assigned to you"
    if t.code == "csm_recommend":
        req.csm_recommends = True
    elif t.code == "csm_not_recommend":
        req.csm_recommends = False
    return outcome


def handle_claim(req, user, data, t, request):
    req.assigned_technician = user
    return Outcome(extra={"technician": user.display_name})


def _store_files(req, user, files, kind):
    stored = []
    for upload in files or []:
        stored.append(save_attachment(req, user, upload, kind))
    return stored


def _apply_specifications(req, user, data):
    specs = data.get("specifications") or {}
    for item in req.items.all():
        spec = (specs.get(item.pk, {}).get("specification") or "").strip()
        cost = specs.get(item.pk, {}).get("cost")
        if not spec:
            raise WorkflowError(f"Write the recommended specification for “{item.description}”.")
        item.recommended_specification = spec
        item.estimated_unit_cost = cost
        item.save(update_fields=["recommended_specification", "estimated_unit_cost"])
    _store_files(req, user, data.get("files"), Attachment.Kind.SPEC_SHEET)
    has_sheet = req.attachments.filter(kind=Attachment.Kind.SPEC_SHEET, removed_at__isnull=True).exists()
    if not has_sheet:
        raise WorkflowError("Attach at least one specification sheet.")
    req.fulfilment_route = Route.PROCUREMENT


def _start_procurement(req, data):
    reference = (data.get("procurement_reference") or "").strip()
    if not reference:
        raise WorkflowError("Enter the Procurement requisition number.")
    req.procurement_reference = reference
    req.sent_to_procurement_at = timezone.now()
    req.next_follow_up_on = add_working_days(timezone.localdate(), settings.ACQ_FOLLOW_UP_INTERVAL_DAYS)
    return reference


def handle_specifications(req, user, data, t, request):
    _apply_specifications(req, user, data)
    extra = {}
    if t.code == "initiate_procurement":
        extra["procurement_reference"] = _start_procurement(req, data)
    return Outcome(extra=extra)


def handle_director_approve(req, user, data, t, request):
    req.approved_at = timezone.now()
    return Outcome(extra={"recorded_in_register": "yes"})


def handle_send_to_procurement(req, user, data, t, request):
    reference = _start_procurement(req, data)
    return Outcome(extra={"procurement_reference": reference})


def handle_follow_up(req, user, data, t, request):
    next_date = data.get("next_follow_up_on")
    today = timezone.localdate()
    if next_date is None:
        next_date = add_working_days(today, settings.ACQ_FOLLOW_UP_INTERVAL_DAYS)
    if next_date <= today:
        raise WorkflowError("The next follow-up date must be after today.")
    req.next_follow_up_on = next_date
    return Outcome(extra={"next_follow_up_on": next_date.isoformat()})


def handle_procurement_update(req, user, data, t, request):
    status = (data.get("procurement_status") or "").strip()
    if not status and not data.get("comment"):
        raise WorkflowError("Give a status or a comment.")
    extra = {}
    if status:
        req.procurement_status = status
        extra["status"] = status
    if data.get("expected_delivery_on"):
        req.expected_delivery_on = data["expected_delivery_on"]
        extra["expected_delivery_on"] = req.expected_delivery_on.isoformat()
    if data.get("purchase_order"):
        extra["purchase_order"] = data["purchase_order"]
    notify_users = [req.assigned_technician] if req.assigned_technician_id else []
    return Outcome(extra=extra, notify_users=notify_users, notify_title=f"{req.reference}: procurement update")


def handle_record_delivery(req, user, data, t, request):
    new_assets = data.get("assets") or []
    created = []
    for fields in new_assets:
        try:
            created.append(
                asset_services.register_asset(
                    performed_by=user,
                    kind="received",
                    requisition=req,
                    note=f"Delivered against {req.reference}",
                    **fields,
                )
            )
        except asset_services.AssetError as exc:
            raise WorkflowError(str(exc)) from exc
    _store_files(req, user, data.get("files"), Attachment.Kind.DELIVERY_NOTE)
    complete = bool(data.get("delivery_complete"))
    if not created and not complete:
        raise WorkflowError("Log at least one delivered item, or mark the delivery complete.")
    if complete and not req.delivered_assets.exists():
        raise WorkflowError("Log the delivered items before you mark the delivery complete.")
    extra = {"assets_logged": ", ".join(a.asset_tag for a in created)}
    if complete:
        req.delivery_completed_at = timezone.now()
        extra["delivery_complete"] = "yes"
        return Outcome(target=State.READY_FOR_ISSUE, extra=extra)
    return Outcome(extra=extra)


def handle_voucher(req, user, data, t, request):
    asset_ids = data.get("asset_ids") or []
    if not asset_ids:
        raise WorkflowError("Choose at least one asset to issue.")
    try:
        assets = asset_services.lock_available(asset_ids)
    except asset_services.AssetError as exc:
        raise WorkflowError(str(exc)) from exc
    replaced = None
    if req.kind == Kind.REPLACEMENT and req.asset_to_replace_id:
        replaced = req.asset_to_replace
        if data.get("replaced_collected") is None:
            raise WorkflowError(f"Say whether the old machine {replaced.asset_tag} was collected.")
        if not data.get("replaced_collected") and not (data.get("replaced_note") or "").strip():
            raise WorkflowError("Explain why the old machine was not collected.")
    if not data.get("campus") or not (data.get("building") or "").strip():
        raise WorkflowError("Give the campus and building where the equipment is going.")
    voucher = asset_services.raise_voucher(
        requisition=req,
        assets=assets,
        issued_by=user,
        campus=data["campus"],
        building=data["building"].strip(),
        office=(data.get("office") or "").strip(),
        notes=(data.get("notes") or "").strip(),
        replaced_asset=replaced,
        replaced_collected=data.get("replaced_collected") if replaced else None,
        replaced_condition=data.get("replaced_condition") or "",
        replaced_note=(data.get("replaced_note") or "").strip(),
    )
    if t.code == "issue_from_stock":
        req.fulfilment_route = Route.STOCK
    if req.assigned_technician_id is None:
        req.assigned_technician = user
    return Outcome(extra={"voucher": voucher.number, "assets": ", ".join(a.asset_tag for a in assets)})


def acknowledgement_statement(req, voucher, user):
    lines = "; ".join(
        f"{line.asset_tag} ({line.make_model}, serial {line.serial_number or 'none'}, "
        f"condition {line.get_condition_display().lower()})"
        for line in voucher.lines.all()
    )
    return (
        f"I, {user.display_name}, confirm that on {timezone.localtime():%d %B %Y at %H:%M} I received the "
        f"following ICT equipment under issue voucher {voucher.number} for request {req.reference}, and that "
        f"the serial numbers, specifications and condition match the voucher: {lines}."
    )


def handle_acknowledge(req, user, data, t, request):
    voucher = req.vouchers.select_for_update().filter(status=IssueVoucher.Status.AWAITING).first()
    if voucher is None:
        raise WorkflowError("There is no voucher waiting for your signature.")
    statement = acknowledgement_statement(req, voucher, user)
    try:
        asset_services.acknowledge_voucher(voucher, user=user, ip=client_ip(request), statement=statement)
    except asset_services.AssetError as exc:
        raise WorkflowError(str(exc)) from exc
    notify_users = [req.assigned_technician] if req.assigned_technician_id else []
    return Outcome(
        extra={"voucher": voucher.number},
        notify_users=notify_users,
        notify_title=f"{req.reference}: {user.display_name} signed voucher {voucher.number}",
    )


def handle_dispute(req, user, data, t, request):
    voucher = req.vouchers.select_for_update().filter(status=IssueVoucher.Status.AWAITING).first()
    if voucher is None:
        raise WorkflowError("There is no voucher waiting for your signature.")
    asset_services.dispute_voucher(voucher, user=user, reason=data.get("comment", "").strip())
    notify_users = [req.assigned_technician] if req.assigned_technician_id else []
    return Outcome(
        extra={"voucher": voucher.number},
        notify_users=notify_users,
        notify_title=f"{req.reference}: {user.display_name} disputed voucher {voucher.number}",
    )


def handle_withdraw_voucher(req, user, data, t, request):
    voucher = req.vouchers.select_for_update().filter(status=IssueVoucher.Status.AWAITING).first()
    if voucher is None:
        raise WorkflowError("There is no open voucher to withdraw.")
    asset_services.withdraw_voucher(voucher, user=user, reason=data.get("comment", "").strip())
    return Outcome(extra={"voucher": voucher.number})


def handle_convert(req, user, data, t, request):
    """No stock (CUT ICT 40 §5.2 fails): open an ICT 38 equipment request for the same need."""
    eqr = Requisition.objects.create(
        kind=Kind.EQUIPMENT,
        title=req.title,
        justification=req.justification,
        priority=req.priority,
        needed_by=req.needed_by,
        requester=req.requester,
        created_by=user,
        department=req.department,
        campus=req.campus,
        building=req.building,
        office=req.office,
        converted_from=req,
        reference=next_reference(Kind.EQUIPMENT),
        submitted_at=timezone.now(),
    )
    for item in req.items.all():
        RequisitionItem.objects.create(
            requisition=eqr,
            category=item.category,
            description=item.description,
            quantity=item.quantity,
            requester_notes=item.requester_notes,
            position=item.position,
        )
    for attachment in req.attachments.filter(removed_at__isnull=True):
        Attachment.objects.create(
            requisition=eqr,
            kind=attachment.kind,
            file=attachment.file.name,
            original_name=attachment.original_name,
            content_type=attachment.content_type,
            size=attachment.size,
            sha256=attachment.sha256,
            uploaded_by=attachment.uploaded_by,
        )
    first = workflow.FIRST_STAGE[Kind.EQUIPMENT]
    _enter_state(eqr, first, State.DRAFT)
    eqr.save()
    entry = record(
        eqr,
        action="created_from_conversion",
        label=f"Raised from allocation request {req.reference} because the item is not in stock",
        actor=user,
        capacity=workflow.capacity(req, user, t),
        from_state=State.DRAFT,
        to_state=first,
        comment=(data.get("comment") or "").strip(),
        sop="CUT ICT 38 §5.1",
        data={"converted_from": req.reference},
        request=request,
    )
    pa_users = [p for p in workflow.current_actors(eqr) if p.pk != user.pk]
    link = reverse("requisitions:detail", args=[eqr.public_id])
    notify(
        pa_users,
        f"{eqr.reference} needs your action: {State(first).label}",
        f"{eqr.title}\nRaised by {user.display_name} for {eqr.requester.display_name}: {entry.comment}",
        link,
    )
    return Outcome(
        extra={"new_reference": eqr.reference},
        notify_users=[req.requester],
        notify_title=f"{req.reference}: not in stock. Equipment request {eqr.reference} raised for you",
    )


HANDLERS = {
    "submit": handle_submit,
    "csm_assign": handle_assign,
    "csm_recommend": handle_assign,
    "csm_not_recommend": handle_assign,
    "reassign": handle_assign,
    "claim": handle_claim,
    "submit_specifications": handle_specifications,
    "initiate_procurement": handle_specifications,
    "director_approve": handle_director_approve,
    "send_to_procurement": handle_send_to_procurement,
    "follow_up": handle_follow_up,
    "procurement_update": handle_procurement_update,
    "record_delivery": handle_record_delivery,
    "issue_from_stock": handle_voucher,
    "raise_voucher": handle_voucher,
    "acknowledge": handle_acknowledge,
    "dispute": handle_dispute,
    "withdraw_voucher": handle_withdraw_voucher,
    "convert": handle_convert,
}


# -- Outside the stage table -------------------------------------------------


def can_view(requisition, user):
    return Requisition.objects.visible_to(user).filter(pk=requisition.pk).exists()


def can_comment(requisition, user):
    if requisition.state == State.DRAFT or requisition.archived_at:
        return False
    if requisition.requester_id == user.pk:
        return True
    return user.is_ict_staff or (
        user.has_role(Role.PROCUREMENT_OFFICER) and requisition.sent_to_procurement_at is not None
    )


def add_note(requisition, user, text, request=None):
    text = (text or "").strip()
    if not text:
        raise WorkflowError("Write a note first.")
    if not can_comment(requisition, user):
        raise WorkflowError("You cannot add notes to this request.")
    with transaction.atomic():
        req = Requisition.objects.select_for_update().get(pk=requisition.pk)
        entry = record(req, action="note", label="Added a note", actor=user, comment=text, request=request)
        link = reverse("requisitions:detail", args=[req.public_id])
        people = workflow.current_actors(req) + [req.requester, req.assigned_technician]
        people = [p for p in people if p is not None and p.pk != user.pk]
        notify(people, f"{req.reference}: new note from {user.display_name}", text, link)
    return entry


def record_edit(requisition, user, changed_fields, request=None):
    """Log changes a requester makes while the request is returned to them."""
    if requisition.reference is None or not changed_fields:
        return None
    with transaction.atomic():
        req = Requisition.objects.select_for_update().get(pk=requisition.pk)
        req.version += 1
        req.save(update_fields=["version", "updated_at"])
        return record(
            req,
            action="edited",
            label="Edited the request",
            actor=user,
            data={"fields": ", ".join(changed_fields)},
            request=request,
        )


# -- Attachments ---------------------------------------------------------------

ALLOWED_TYPES = {
    ".pdf": ("application/pdf", [b"%PDF-"]),
    ".png": ("image/png", [b"\x89PNG\r\n\x1a\n"]),
    ".jpg": ("image/jpeg", [b"\xff\xd8\xff"]),
    ".jpeg": ("image/jpeg", [b"\xff\xd8\xff"]),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", [b"PK\x03\x04"]),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", [b"PK\x03\x04"]),
    ".doc": ("application/msword", [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"]),
    ".xls": ("application/vnd.ms-excel", [b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"]),
}
ALLOWED_EXTENSIONS_TEXT = "PDF, Word, Excel, PNG or JPEG"


def validate_upload(upload):
    """Check extension, size and the file's first bytes. Raise ValidationError."""
    name = os.path.basename(upload.name or "")
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_TYPES:
        raise ValidationError(f"{name}: only {ALLOWED_EXTENSIONS_TEXT} files can be attached.")
    limit = settings.ACQ_UPLOAD_MAX_MB * 1024 * 1024
    if upload.size > limit:
        raise ValidationError(f"{name} is larger than {settings.ACQ_UPLOAD_MAX_MB} MB.")
    if upload.size == 0:
        raise ValidationError(f"{name} is empty.")
    upload.seek(0)
    head = upload.read(16)
    upload.seek(0)
    content_type, signatures = ALLOWED_TYPES[ext]
    if not any(head.startswith(sig) for sig in signatures):
        raise ValidationError(f"{name} does not look like a real {ext[1:].upper()} file.")
    return content_type


def save_attachment(requisition, user, upload, kind):
    try:
        content_type = validate_upload(upload)
    except ValidationError as exc:
        raise WorkflowError(exc.messages[0]) from exc
    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    attachment = Attachment(
        requisition=requisition,
        kind=kind,
        original_name=os.path.basename(upload.name)[:255],
        content_type=content_type or mimetypes.guess_type(upload.name)[0] or "application/octet-stream",
        size=upload.size,
        sha256=digest.hexdigest(),
        uploaded_by=user,
        uploaded_at=timezone.now(),
    )
    attachment.file.save(upload.name, upload, save=False)
    attachment.save()
    if requisition.reference:
        record(
            requisition,
            action="attachment_added",
            label="Added an attachment",
            actor=user,
            data={"file": attachment.original_name, "kind": attachment.get_kind_display(), "sha256": attachment.sha256},
        )
    return attachment


def can_upload(requisition, user):
    if requisition.archived_at:
        return False
    if requisition.requester_id == user.pk and requisition.is_editable:
        return True
    if requisition.requester_id != user.pk and user.has_role(Role.TECHNICIAN):
        return requisition.state in workflow.FULFILMENT_STATES
    return False


def can_remove_attachment(attachment, user):
    req = attachment.requisition
    if attachment.removed_at or attachment.uploaded_by_id != user.pk or req.archived_at:
        return False
    if req.requester_id == user.pk:
        return req.is_editable
    return req.state == State.TECHNICIAN_REVIEW and user.has_role(Role.TECHNICIAN)


def remove_attachment(attachment, user):
    req = attachment.requisition
    if not can_remove_attachment(attachment, user):
        raise WorkflowError("You cannot remove this attachment now.")
    with transaction.atomic():
        attachment.removed_at = timezone.now()
        attachment.removed_by = user
        attachment.save(update_fields=["removed_at", "removed_by"])
        if req.reference:
            record(
                req,
                action="attachment_removed",
                label="Removed an attachment",
                actor=user,
                data={"file": attachment.original_name},
            )


# -- Assets the requester holds, for replacement requests ------------------------


def replaceable_assets(user):
    return user.assets_held.filter(status=AssetStatus.ALLOCATED).select_related("category")
