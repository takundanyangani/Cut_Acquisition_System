"""Read-only JSON API for other CUT systems (ERP, asset management, portal).

Authenticate with `Authorization: Bearer <key>`. Keys come from
`manage.py create_api_key`. See docs/07-integration.md.
"""

from functools import wraps

from django.core.paginator import Paginator
from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

from apps.assets.models import Asset, IssueVoucher
from apps.requisitions.models import Requisition, State
from apps.requisitions.workflow import stage_holder

from .models import ApiKey

MAX_PAGE_SIZE = 200


def _error(status, message):
    return JsonResponse({"error": message}, status=status)


def api_key_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        scheme, _, raw = header.partition(" ")
        if scheme.lower() != "bearer" or not raw.strip():
            return _error(401, "Send an API key in the Authorization header: Bearer <key>.")
        key = ApiKey.authenticate(raw.strip())
        if key is None:
            return _error(401, "The API key is not valid or has been revoked.")
        request.api_key = key
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    return require_GET(wrapper)


def _iso(value):
    return value.isoformat() if value else None


def _paginate(request, queryset, serialise):
    try:
        size = min(int(request.GET.get("page_size", 50)), MAX_PAGE_SIZE)
        page_number = int(request.GET.get("page", 1))
    except ValueError:
        return _error(400, "page and page_size must be whole numbers.")
    paginator = Paginator(queryset, max(size, 1))
    page = paginator.get_page(page_number)
    return JsonResponse(
        {
            "count": paginator.count,
            "page": page.number,
            "pages": paginator.num_pages,
            "results": [serialise(obj) for obj in page.object_list],
        }
    )


def _updated_since(request, queryset):
    raw = request.GET.get("updated_since")
    if raw:
        moment = parse_datetime(raw)
        if moment is None:
            return None
        queryset = queryset.filter(updated_at__gte=moment)
    return queryset


def serialise_requisition(req, detail=False):
    data = {
        "reference": req.reference,
        "type": req.kind,
        "type_label": req.get_kind_display(),
        "procedure": req.procedure_number,
        "title": req.title,
        "state": req.state,
        "state_label": State(req.state).label,
        "with": stage_holder(req),
        "requester": {
            "name": req.requester.display_name,
            "email": req.requester.email,
            "staff_number": req.requester.staff_number,
        },
        "department": str(req.department) if req.department_id else None,
        "location": req.location_text,
        "priority": req.priority,
        "procurement_reference": req.procurement_reference or None,
        "submitted_at": _iso(req.submitted_at),
        "approved_at": _iso(req.approved_at),
        "sent_to_procurement_at": _iso(req.sent_to_procurement_at),
        "closed_at": _iso(req.closed_at),
        "archived": req.archived_at is not None,
        "updated_at": _iso(req.updated_at),
    }
    if detail:
        data["items"] = [
            {
                "category": item.category.name,
                "description": item.description,
                "quantity": item.quantity,
                "recommended_specification": item.recommended_specification,
                "estimated_unit_cost": str(item.estimated_unit_cost) if item.estimated_unit_cost is not None else None,
            }
            for item in req.items.select_related("category")
        ]
        data["vouchers"] = [v.number for v in req.vouchers.all()]
        data["routing"] = [
            {
                "seq": e.seq,
                "action": e.action,
                "label": e.label,
                "by": e.actor_name,
                "role": e.actor_role_label,
                "on_behalf_of": e.on_behalf_of_name or None,
                "from_state": e.from_state or None,
                "to_state": e.to_state or None,
                "signed": e.signed,
                "at": _iso(e.created_at),
                "hash": e.entry_hash,
            }
            for e in req.audit_entries.order_by("seq")
            if e.action != "note"
        ]
    return data


def serialise_asset(asset):
    return {
        "asset_tag": asset.asset_tag,
        "serial_number": asset.serial_number,
        "category": asset.category.name,
        "make": asset.make,
        "model": asset.model,
        "specification": asset.specification,
        "condition": asset.condition,
        "status": asset.status,
        "holder": (
            {"name": asset.holder.display_name, "email": asset.holder.email, "staff_number": asset.holder.staff_number}
            if asset.holder_id
            else None
        ),
        "location": asset.location_text,
        "source_request": asset.source_requisition.reference if asset.source_requisition_id else None,
        "acquired_on": _iso(asset.acquired_on),
        "warranty_expires_on": _iso(asset.warranty_expires_on),
        "updated_at": _iso(asset.updated_at),
    }


def serialise_voucher(voucher):
    return {
        "number": voucher.number,
        "request": voucher.requisition.reference,
        "status": voucher.status,
        "issued_to": voucher.issued_to.display_name,
        "issued_by": voucher.issued_by.display_name,
        "location": voucher.location_text,
        "created_at": _iso(voucher.created_at),
        "acknowledged_at": _iso(voucher.acknowledged_at),
        "acknowledgement": voucher.acknowledgement_text or None,
        "lines": [
            {
                "asset_tag": line.asset_tag,
                "serial_number": line.serial_number or None,
                "category": line.category_name,
                "make_model": line.make_model,
                "specification": line.specification,
                "condition": line.condition,
            }
            for line in voucher.lines.all()
        ],
    }


@api_key_required
def requisition_list(request):
    queryset = Requisition.objects.exclude(state=State.DRAFT).select_related(
        "requester", "department", "campus", "assigned_technician"
    )
    if request.GET.get("state"):
        queryset = queryset.filter(state=request.GET["state"])
    if request.GET.get("type"):
        queryset = queryset.filter(kind=request.GET["type"].upper())
    queryset = _updated_since(request, queryset)
    if queryset is None:
        return _error(400, "updated_since must be an ISO 8601 date and time.")
    return _paginate(request, queryset.order_by("submitted_at", "pk"), serialise_requisition)


@api_key_required
def requisition_detail(request, reference):
    req = (
        Requisition.objects.exclude(state=State.DRAFT)
        .select_related("requester", "department", "campus", "assigned_technician")
        .filter(reference__iexact=reference)
        .first()
    )
    if req is None:
        return _error(404, "No request with that reference.")
    return JsonResponse(serialise_requisition(req, detail=True))


@api_key_required
def asset_list(request):
    queryset = Asset.objects.select_related("category", "holder", "campus", "source_requisition")
    if request.GET.get("status"):
        queryset = queryset.filter(status=request.GET["status"])
    queryset = _updated_since(request, queryset)
    if queryset is None:
        return _error(400, "updated_since must be an ISO 8601 date and time.")
    return _paginate(request, queryset.order_by("asset_tag"), serialise_asset)


@api_key_required
def asset_detail(request, tag):
    asset = (
        Asset.objects.select_related("category", "holder", "campus", "source_requisition")
        .filter(asset_tag__iexact=tag)
        .first()
    )
    if asset is None:
        return _error(404, "No asset with that tag.")
    return JsonResponse(serialise_asset(asset))


@api_key_required
def voucher_detail(request, number):
    voucher = (
        IssueVoucher.objects.select_related("requisition", "issued_to", "issued_by", "campus")
        .filter(number__iexact=number)
        .first()
    )
    if voucher is None:
        return _error(404, "No voucher with that number.")
    return JsonResponse(serialise_voucher(voucher))
