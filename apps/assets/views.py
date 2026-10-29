from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Role

from . import services
from .forms import AssetFilterForm, AssetForm, StatusChangeForm
from .models import MANUAL_STATUS_CHANGES, Asset, AssetStatus, IssueVoucher

ASSET_EDITORS = {Role.TECHNICIAN, Role.CLIENT_SERVICES_MANAGER}


def _require_ict(user):
    if not user.is_ict_staff:
        raise PermissionDenied


def _require_editor(user):
    if not user.has_any_role(ASSET_EDITORS):
        raise PermissionDenied


def _filtered_assets(request):
    form = AssetFilterForm(request.GET or None)
    queryset = Asset.objects.select_related("category", "holder", "campus")
    if form.is_valid():
        q = form.cleaned_data.get("q", "").strip()
        if q:
            queryset = queryset.filter(
                Q(asset_tag__icontains=q)
                | Q(serial_number__icontains=q)
                | Q(make__icontains=q)
                | Q(model__icontains=q)
                | Q(holder__first_name__icontains=q)
                | Q(holder__last_name__icontains=q)
            )
        if form.cleaned_data.get("status"):
            queryset = queryset.filter(status=form.cleaned_data["status"])
        if form.cleaned_data.get("category"):
            queryset = queryset.filter(category=form.cleaned_data["category"])
    return form, queryset


@login_required
def asset_list(request):
    _require_ict(request.user)
    form, queryset = _filtered_assets(request)
    page = Paginator(queryset.order_by("asset_tag"), 30).get_page(request.GET.get("page"))
    counts = {s: 0 for s in AssetStatus}
    for status in Asset.objects.values_list("status", flat=True):
        counts[status] = counts.get(status, 0) + 1
    return render(
        request,
        "assets/list.html",
        {
            "page": page,
            "filter_form": form,
            "can_edit": request.user.has_any_role(ASSET_EDITORS),
            "status_counts": [(AssetStatus(s), counts.get(s, 0)) for s in AssetStatus],
        },
    )


@login_required
def export_assets(request):
    _require_ict(request.user)
    from apps.requisitions.exports import assets_workbook

    _, queryset = _filtered_assets(request)
    return assets_workbook(queryset.order_by("asset_tag"))


@login_required
def asset_detail(request, tag):
    asset = get_object_or_404(Asset.objects.select_related("category", "holder", "campus"), asset_tag=tag)
    user = request.user
    if not (user.is_ict_staff or asset.holder_id == user.pk):
        raise Http404("No such asset")
    can_edit = user.has_any_role(ASSET_EDITORS)
    status_form = None
    if can_edit and MANUAL_STATUS_CHANGES.get(AssetStatus(asset.status)):
        status_form = StatusChangeForm(asset=asset)
    return render(
        request,
        "assets/detail.html",
        {
            "asset": asset,
            "movements": asset.movements.select_related(
                "performed_by", "from_holder", "to_holder", "voucher", "requisition"
            ),
            "voucher_lines": asset.voucher_lines.select_related("voucher"),
            "can_edit": can_edit,
            "status_form": status_form,
        },
    )


@login_required
def asset_create(request):
    _require_editor(request.user)
    form = AssetForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        fields = {name: form.cleaned_data[name] for name in form.fields}
        try:
            with transaction.atomic():
                asset = services.register_asset(performed_by=request.user, note="Registered by hand", **fields)
        except services.AssetError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"{asset.asset_tag} added to the register as in stock.")
            return redirect(asset.get_absolute_url())
    return render(request, "assets/form.html", {"form": form, "asset": None})


@login_required
def asset_edit(request, tag):
    _require_editor(request.user)
    asset = get_object_or_404(Asset, asset_tag=tag)
    form = AssetForm(request.POST or None, instance=asset)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"{asset.asset_tag} updated.")
        return redirect(asset.get_absolute_url())
    return render(request, "assets/form.html", {"form": form, "asset": asset})


@login_required
def asset_status(request, tag):
    _require_editor(request.user)
    asset = get_object_or_404(Asset, asset_tag=tag)
    if request.method != "POST":
        return redirect(asset.get_absolute_url())
    form = StatusChangeForm(request.POST, asset=asset)
    if form.is_valid():
        try:
            services.change_status(
                asset, form.cleaned_data["status"], performed_by=request.user, note=form.cleaned_data["note"]
            )
        except services.AssetError as exc:
            messages.error(request, str(exc))
        else:
            asset.refresh_from_db()
            messages.success(request, f"{asset.asset_tag} is now {asset.get_status_display().lower()}.")
    else:
        messages.error(request, "Choose a new status and give a reason.")
    return redirect(asset.get_absolute_url())


@login_required
def voucher_list(request):
    user = request.user
    queryset = IssueVoucher.objects.select_related("issued_to", "issued_by", "requisition")
    if not user.is_ict_staff:
        queryset = queryset.filter(issued_to=user)
    status = request.GET.get("status")
    if status in IssueVoucher.Status.values:
        queryset = queryset.filter(status=status)
    page = Paginator(queryset.order_by("-created_at"), 25).get_page(request.GET.get("page"))
    return render(
        request,
        "assets/voucher_list.html",
        {"page": page, "status": status, "statuses": IssueVoucher.Status.choices},
    )


def _voucher_for(user, number):
    voucher = get_object_or_404(
        IssueVoucher.objects.select_related("issued_to", "issued_by", "requisition", "campus", "replaced_asset"),
        number=number,
    )
    if not (user.is_ict_staff or voucher.issued_to_id == user.pk):
        raise Http404("No such voucher")
    return voucher


@login_required
def voucher_detail(request, number):
    voucher = _voucher_for(request.user, number)
    return render(request, "assets/voucher_detail.html", {"voucher": voucher, "lines": voucher.lines.all()})


@login_required
def voucher_print(request, number):
    voucher = _voucher_for(request.user, number)
    return render(request, "assets/voucher_print.html", {"voucher": voucher, "lines": voucher.lines.all()})
