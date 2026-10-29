from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import REPORT_ROLES, Role

from . import services, workflow
from .forms import FORMS, AttachmentForm, ItemFormSet, NoteForm, RequestFilterForm, RequisitionForm
from .models import KIND_PROCEDURE, OPEN_STATES, Attachment, Kind, Requisition, State, verify_chain

KIND_SLUGS = {"allocation": Kind.ALLOCATION, "equipment": Kind.EQUIPMENT, "replacement": Kind.REPLACEMENT}
SLUG_FOR_KIND = {v: k for k, v in KIND_SLUGS.items()}


def _visible_or_404(user, public_id):
    try:
        return (
            Requisition.objects.visible_to(user)
            .select_related("requester", "assigned_technician", "campus", "department", "asset_to_replace")
            .get(public_id=public_id)
        )
    except Requisition.DoesNotExist as exc:
        raise Http404("No such request") from exc


def _base_list(user):
    return (
        Requisition.objects.visible_to(user)
        .select_related("requester", "assigned_technician", "campus", "department")
        .prefetch_related("items")
    )


def _apply_filters(queryset, form):
    if not form.is_valid():
        return queryset
    q = form.cleaned_data.get("q", "").strip()
    if q:
        queryset = queryset.filter(
            Q(reference__icontains=q)
            | Q(title__icontains=q)
            | Q(requester__first_name__icontains=q)
            | Q(requester__last_name__icontains=q)
            | Q(procurement_reference__icontains=q)
        )
    if form.cleaned_data.get("kind"):
        queryset = queryset.filter(kind=form.cleaned_data["kind"])
    if form.cleaned_data.get("state"):
        queryset = queryset.filter(state=form.cleaned_data["state"])
    return queryset


def _page(request, queryset, per_page=25):
    paginator = Paginator(queryset, per_page)
    return paginator.get_page(request.GET.get("page"))


@login_required
def my_requests(request):
    form = RequestFilterForm(request.GET or None)
    queryset = _apply_filters(Requisition.objects.filter(requester=request.user).active(), form)
    queryset = queryset.select_related("assigned_technician").prefetch_related("items").order_by("-updated_at")
    return render(
        request,
        "requisitions/list.html",
        {"page": _page(request, queryset), "filter_form": form, "tab": "mine", "heading": "My requests"},
    )


@login_required
def queue(request):
    items = workflow.queue_for(request.user)
    return render(
        request,
        "requisitions/list.html",
        {
            "page": _page(request, items),
            "tab": "queue",
            "heading": "Awaiting my action",
            "intro": "Requests where you are the next person to act. The oldest due date is first.",
        },
    )


@login_required
def all_requests(request):
    user = request.user
    if not (user.is_ict_staff or user.has_role(Role.PROCUREMENT_OFFICER)):
        raise PermissionDenied
    form = RequestFilterForm(request.GET or None)
    queryset = _apply_filters(_base_list(user).active().exclude(state=State.DRAFT), form)
    if request.GET.get("overdue") == "1":
        queryset = queryset.filter(state__in=list(OPEN_STATES), state_due_at__lt=timezone.now())
    queryset = queryset.order_by("-submitted_at")
    return render(
        request,
        "requisitions/list.html",
        {
            "page": _page(request, queryset),
            "filter_form": form,
            "tab": "all",
            "heading": "All requests",
            "can_export": user.has_any_role(REPORT_ROLES),
        },
    )


@login_required
def archive(request):
    user = request.user
    if not user.has_any_role({Role.AUDITOR, Role.SYSTEM_ADMIN, Role.CLIENT_SERVICES_MANAGER, Role.ICT_DIRECTOR}):
        raise PermissionDenied
    form = RequestFilterForm(request.GET or None)
    queryset = _apply_filters(_base_list(user).filter(archived_at__isnull=False), form).order_by("-closed_at")
    return render(
        request,
        "requisitions/list.html",
        {
            "page": _page(request, queryset),
            "filter_form": form,
            "tab": "archive",
            "heading": "Archive",
            "intro": "Closed requests past their retention period. They are read-only and are never deleted.",
        },
    )


@login_required
def choose_kind(request):
    return render(
        request,
        "requisitions/choose_kind.html",
        {
            "options": [
                {
                    "slug": "allocation",
                    "kind": Kind.ALLOCATION,
                    "procedure": KIND_PROCEDURE[Kind.ALLOCATION][0],
                    "icon": "package",
                    "cta": "Start an allocation request",
                    "summary": "An item you need now that ICT may already hold: laptop, monitor, "
                    "projector, headset, mouse or keyboard.",
                    "route": "Helpdesk checks stock. If it is there you sign an issue voucher. "
                    "If not, the Helpdesk raises an equipment request for you.",
                },
                {
                    "slug": "equipment",
                    "kind": Kind.EQUIPMENT,
                    "procedure": KIND_PROCEDURE[Kind.EQUIPMENT][0],
                    "icon": "file-plus",
                    "cta": "Start an equipment request",
                    "summary": "A new requirement that will probably need buying, such as "
                    "equipment for a new office, lab or project.",
                    "route": "Personal Advisor, ICT Director, Client Services Manager, technician "
                    "specifications, Operations Manager, ICT Director approval, Procurement.",
                },
                {
                    "slug": "replacement",
                    "kind": Kind.REPLACEMENT,
                    "procedure": KIND_PROCEDURE[Kind.REPLACEMENT][0],
                    "icon": "refresh-cw",
                    "cta": "Start a replacement request",
                    "summary": "Your current machine is faulty, damaged, lost or too old for your work.",
                    "route": "Client Services Manager recommendation, ICT Director approval, "
                    "specification sheet, Procurement.",
                },
            ]
        },
    )


def _save_items(formset, requisition):
    formset.instance = requisition
    items = formset.save(commit=False)
    for obj in formset.deleted_objects:
        obj.delete()
    for position, item in enumerate(items):
        item.requisition = requisition
        item.position = position
        item.save()
    return items


def _changed_fields(form, formset):
    changed = list(form.changed_data)
    changed = [name for name in changed if name != "attachments"]
    if formset.has_changed():
        changed.append("items")
    return changed


@login_required
def create(request, kind_slug):
    kind = KIND_SLUGS.get(kind_slug)
    if kind is None:
        raise Http404("Unknown request type")
    user = request.user
    requisition = Requisition(kind=kind, requester=user, created_by=user, department=user.department)
    if request.method == "POST":
        form = RequisitionForm(request.POST, request.FILES, instance=requisition, kind=kind, user=user)
        formset = ItemFormSet(request.POST, instance=requisition, prefix="items")
        if form.is_valid() and formset.is_valid():
            try:
                with transaction.atomic():
                    requisition = form.save()
                    _save_items(formset, requisition)
                    for upload in form.cleaned_data.get("attachments") or []:
                        services.save_attachment(requisition, user, upload, Attachment.Kind.SUPPORTING)
            except services.WorkflowError as exc:
                form.add_error("attachments", str(exc))
            else:
                return _after_save(request, requisition)
    else:
        form = RequisitionForm(instance=requisition, kind=kind, user=user)
        formset = ItemFormSet(instance=requisition, prefix="items")
    return render(
        request,
        "requisitions/form.html",
        {
            "form": form,
            "formset": formset,
            "kind": kind,
            "requisition": None,
            "procedure": KIND_PROCEDURE[kind],
            "kind_slug": kind_slug,
        },
    )


def _after_save(request, requisition):
    if "submit" in request.POST:
        try:
            services.perform(
                requisition, request.user, "submit", data={"comment": request.POST.get("comment", "")}, request=request
            )
        except services.WorkflowError as exc:
            messages.error(request, f"Saved as a draft but not submitted: {exc}")
            return redirect("requisitions:edit", public_id=requisition.public_id)
        requisition.refresh_from_db()
        messages.success(
            request,
            f"{requisition.reference} submitted. It is now with: {workflow.stage_holder(requisition)}.",
        )
    else:
        messages.success(request, "Draft saved. Nobody else can see it until you submit it.")
    return redirect(requisition.get_absolute_url())


@login_required
def edit(request, public_id):
    requisition = _visible_or_404(request.user, public_id)
    if requisition.requester_id != request.user.pk:
        raise PermissionDenied
    if not requisition.is_editable:
        messages.info(request, "This request can no longer be edited.")
        return redirect(requisition.get_absolute_url())
    kind = Kind(requisition.kind)
    if request.method == "POST":
        form = RequisitionForm(request.POST, request.FILES, instance=requisition, kind=kind, user=request.user)
        formset = ItemFormSet(request.POST, instance=requisition, prefix="items")
        if form.is_valid() and formset.is_valid():
            changed = _changed_fields(form, formset)
            try:
                with transaction.atomic():
                    locked = Requisition.objects.select_for_update().get(pk=requisition.pk)
                    if not locked.is_editable:
                        raise services.WorkflowError("This request moved on and can no longer be edited.")
                    requisition = form.save()
                    _save_items(formset, requisition)
                    for upload in form.cleaned_data.get("attachments") or []:
                        services.save_attachment(requisition, request.user, upload, Attachment.Kind.SUPPORTING)
                    services.record_edit(requisition, request.user, changed, request=request)
            except services.WorkflowError as exc:
                messages.error(request, str(exc))
                return redirect(requisition.get_absolute_url())
            return _after_save(request, requisition)
    else:
        form = RequisitionForm(instance=requisition, kind=kind, user=request.user)
        formset = ItemFormSet(instance=requisition, prefix="items")
    return render(
        request,
        "requisitions/form.html",
        {
            "form": form,
            "formset": formset,
            "kind": kind,
            "requisition": requisition,
            "procedure": KIND_PROCEDURE[kind],
            "kind_slug": SLUG_FOR_KIND[kind],
        },
    )


@login_required
@require_POST
def discard(request, public_id):
    requisition = _visible_or_404(request.user, public_id)
    if requisition.requester_id != request.user.pk or requisition.state != State.DRAFT or requisition.reference:
        raise PermissionDenied
    for attachment in requisition.attachments.all():
        attachment.file.delete(save=False)
    requisition.delete()
    messages.success(request, "Draft discarded.")
    return redirect("requisitions:mine")


@login_required
def detail(request, public_id):
    user = request.user
    requisition = _visible_or_404(user, public_id)
    actions = workflow.available_transitions(requisition, user)
    primary_actions = [(t, c) for t, c in actions if t.primary]
    other_actions = [(t, c) for t, c in actions if not t.primary]
    entries = list(requisition.audit_entries.select_related("actor", "on_behalf_of").order_by("seq"))
    vouchers = requisition.vouchers.prefetch_related("lines").select_related("issued_by", "issued_to", "campus")
    attachments = [a for a in requisition.attachments.select_related("uploaded_by") if a.removed_at is None]
    context = {
        "req": requisition,
        "items": list(requisition.items.select_related("category")),
        "attachments": attachments,
        "removable_ids": {a.pk for a in attachments if services.can_remove_attachment(a, user)},
        "last_return": next((e for e in reversed(entries) if e.to_state == State.RETURNED), None),
        "entries": entries,
        "slip": [e for e in entries if e.action not in {"note", "attachment_added", "attachment_removed"}],
        "notes": [e for e in entries if e.action == "note"],
        "vouchers": vouchers,
        "delivered_assets": requisition.delivered_assets.select_related("category"),
        "route": workflow.route_map(requisition),
        "primary_actions": primary_actions,
        "other_actions": other_actions,
        "holder": workflow.stage_holder(requisition),
        "blocked": workflow.blocked_reason(requisition),
        "can_comment": services.can_comment(requisition, user),
        "can_upload": services.can_upload(requisition, user),
        "note_form": NoteForm(),
        "attachment_form": AttachmentForm(),
        "converted_to": requisition.converted_requests.first(),
        "is_requester": requisition.requester_id == user.pk,
        "target_days": settings.ACQ_STAGE_TARGET_DAYS.get(requisition.state),
    }
    return render(request, "requisitions/detail.html", context)


@login_required
def print_record(request, public_id):
    requisition = _visible_or_404(request.user, public_id)
    if requisition.reference is None:
        raise Http404("Drafts have no record")
    return render(
        request,
        "requisitions/print.html",
        {
            "req": requisition,
            "items": requisition.items.select_related("category"),
            "entries": requisition.audit_entries.order_by("seq"),
            "vouchers": requisition.vouchers.prefetch_related("lines"),
            "chain_problems": verify_chain(requisition),
        },
    )


@login_required
def action(request, public_id, code):
    user = request.user
    requisition = _visible_or_404(user, public_id)
    transition = workflow.find_transition(requisition, code)
    if transition is None:
        messages.info(request, "That action is not available at this stage. The request may have moved on.")
        return redirect(requisition.get_absolute_url())
    capacity = workflow.capacity(requisition, user, transition)
    if capacity is None:
        raise PermissionDenied
    signing = transition.sign and settings.ACQ_SIGNATURE_REQUIRES_PASSWORD
    form_class = FORMS[transition.form]
    kwargs = {"transition": transition, "requisition": requisition, "user": user, "signing": signing}
    if transition.form == "voucher":
        kwargs["show_all"] = (request.GET.get("all") or request.POST.get("show_all")) == "1"
    if request.method == "POST":
        form = form_class(data=request.POST, files=request.FILES, **kwargs)
        if form.is_valid():
            try:
                services.perform(
                    requisition,
                    user,
                    code,
                    data=form.workflow_data(),
                    expected_version=form.cleaned_data["version"],
                    request=request,
                    password=form.cleaned_data.get("password"),
                )
            except services.StaleRequisition as exc:
                messages.warning(request, str(exc))
                return redirect(requisition.get_absolute_url())
            except services.SignatureError as exc:
                form.add_error("password", str(exc))
            except services.WorkflowError as exc:
                form.add_error(None, str(exc))
            else:
                requisition.refresh_from_db()
                messages.success(request, f"{requisition.reference}: {transition.done}.")
                return redirect(requisition.get_absolute_url())
    else:
        form = form_class(**kwargs)
    statement = workflow.signing_statement(requisition, user, transition, capacity) if signing else ""
    return render(
        request,
        "requisitions/action.html",
        {
            "req": requisition,
            "transition": transition,
            "capacity": capacity,
            "form": form,
            "signing": signing,
            "statement": statement,
            "items": requisition.items.select_related("category"),
            "voucher": requisition.open_voucher if transition.form == "acknowledge" or code == "dispute" else None,
            "show_all": kwargs.get("show_all", False),
        },
    )


@login_required
@require_POST
def add_note(request, public_id):
    requisition = _visible_or_404(request.user, public_id)
    form = NoteForm(request.POST)
    if form.is_valid():
        try:
            services.add_note(requisition, request.user, form.cleaned_data["note"], request=request)
        except services.WorkflowError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Note added.")
    else:
        messages.error(request, "Write a note first.")
    return redirect(f"{requisition.get_absolute_url()}#notes")


@login_required
@require_POST
def upload(request, public_id):
    requisition = _visible_or_404(request.user, public_id)
    if not services.can_upload(requisition, request.user):
        raise PermissionDenied
    form = AttachmentForm(request.POST, request.FILES)
    if form.is_valid():
        saved = 0
        try:
            with transaction.atomic():
                for upload_file in form.cleaned_data["files"]:
                    services.save_attachment(requisition, request.user, upload_file, form.cleaned_data["kind"])
                    saved += 1
        except services.WorkflowError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"{saved} file{'s' if saved != 1 else ''} attached.")
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect(f"{requisition.get_absolute_url()}#attachments")


@login_required
@require_POST
def remove_attachment(request, public_id):
    attachment = get_object_or_404(Attachment.objects.select_related("requisition"), public_id=public_id)
    if not services.can_view(attachment.requisition, request.user):
        raise Http404("No such attachment")
    try:
        services.remove_attachment(attachment, request.user)
    except services.WorkflowError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{attachment.original_name} removed. The audit trail keeps a record.")
    return redirect(f"{attachment.requisition.get_absolute_url()}#attachments")


@login_required
def download_attachment(request, public_id):
    attachment = get_object_or_404(Attachment.objects.select_related("requisition"), public_id=public_id)
    if not services.can_view(attachment.requisition, request.user):
        raise Http404("No such attachment")
    try:
        handle = attachment.file.open("rb")
    except FileNotFoundError as exc:
        raise Http404("The file is missing from storage") from exc
    response = FileResponse(
        handle, as_attachment=True, filename=attachment.original_name, content_type=attachment.content_type
    )
    response["Cache-Control"] = "private, no-store"
    return response


@login_required
def by_reference(request, reference):
    requisition = Requisition.objects.visible_to(request.user).filter(reference__iexact=reference).first()
    if requisition is None:
        raise Http404("No such request")
    return redirect(requisition.get_absolute_url())


@login_required
def search(request):
    from apps.assets.models import Asset

    q = (request.GET.get("q") or "").strip()
    if not q:
        return redirect("core:dashboard")
    user = request.user
    match = Requisition.objects.visible_to(user).filter(reference__iexact=q).first()
    if match:
        return redirect(match.get_absolute_url())
    if user.is_ict_staff:
        asset = Asset.objects.filter(Q(asset_tag__iexact=q) | Q(serial_number__iexact=q)).first()
        if asset:
            return redirect(asset.get_absolute_url())
    queryset = (
        _base_list(user)
        .filter(Q(reference__icontains=q) | Q(title__icontains=q) | Q(procurement_reference__icontains=q))
        .exclude(Q(state=State.DRAFT) & ~Q(requester=user))
        .order_by("-created_at")
    )
    return render(
        request,
        "requisitions/list.html",
        {"page": _page(request, queryset), "tab": "search", "heading": f"Results for “{q}”", "query": q},
    )


@login_required
def export_requests(request):
    if not request.user.has_any_role(REPORT_ROLES):
        raise PermissionDenied
    from .exports import requests_workbook

    form = RequestFilterForm(request.GET or None)
    queryset = _apply_filters(_base_list(request.user).exclude(state=State.DRAFT), form)
    if request.GET.get("archived") != "1":
        queryset = queryset.active()
    return requests_workbook(queryset.order_by("submitted_at"))
