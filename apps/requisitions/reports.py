"""Figures for the KPI page. Each figure maps to a KPI in the SOPs:

- CUT ICT 38 KPI 6.1: user requests are responded to quickly
  (time per stage and time to Procurement).
- CUT ICT 40 KPI 7.1: controlled movement of assets (signed vouchers).
- CUT ICT 42 KPI 7.1: asset movements approved by ICT personnel (movement log).
"""

from collections import Counter, defaultdict
from datetime import timedelta
from statistics import median

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from apps.assets.models import AssetMovement, IssueVoucher, MovementKind
from apps.core.workdays import working_days_between

from .models import OPEN_STATES, AuditEntry, Kind, Requisition, State

STAGE_ORDER = [
    State.PA_REVIEW,
    State.DIRECTOR_REVIEW,
    State.CSM_REVIEW,
    State.TECHNICIAN_REVIEW,
    State.OPS_REVIEW,
    State.DIRECTOR_APPROVAL,
    State.APPROVED,
    State.WITH_PROCUREMENT,
    State.READY_FOR_ISSUE,
    State.AWAITING_ACK,
    State.RETURNED,
]


def _median(values):
    return round(float(median(values)), 1) if values else None


def open_by_stage():
    now = timezone.now()
    rows = []
    queryset = Requisition.objects.active().filter(state__in=OPEN_STATES)
    counts = dict(queryset.values_list("state").annotate(n=Count("pk")))
    overdue = dict(queryset.filter(state_due_at__lt=now).values_list("state").annotate(n=Count("pk")))
    for state in STAGE_ORDER:
        total = counts.get(state, 0)
        rows.append(
            {
                "state": state,
                "label": State(state).label,
                "count": total,
                "overdue": overdue.get(state, 0),
                "target": settings.ACQ_STAGE_TARGET_DAYS.get(state),
            }
        )
    return rows


def stage_durations(since):
    """Median and longest working days spent in each stage, for stages left after `since`."""
    entries = (
        AuditEntry.objects.filter(requisition__submitted_at__isnull=False)
        .exclude(from_state="")
        .exclude(to_state="")
        .order_by("requisition_id", "seq")
        .values("requisition_id", "from_state", "to_state", "created_at")
    )
    durations = defaultdict(list)
    entered = {}
    for e in entries:
        key = e["requisition_id"]
        if e["from_state"] == e["to_state"]:
            continue
        start = entered.get((key, e["from_state"]))
        if start is not None and e["created_at"] >= since:
            durations[e["from_state"]].append(working_days_between(start, e["created_at"]))
        entered[(key, e["to_state"])] = e["created_at"]
    rows = []
    for state in STAGE_ORDER:
        values = durations.get(state, [])
        rows.append(
            {
                "state": state,
                "label": State(state).label,
                "samples": len(values),
                "median": _median(values),
                "longest": max(values) if values else None,
                "target": settings.ACQ_STAGE_TARGET_DAYS.get(state),
            }
        )
    return rows


def turnaround(since):
    """Working days from submission to Procurement and to the user's signature."""
    to_procurement = []
    for req in Requisition.objects.filter(sent_to_procurement_at__gte=since, submitted_at__isnull=False):
        to_procurement.append(working_days_between(req.submitted_at, req.sent_to_procurement_at))
    to_completion = defaultdict(list)
    for req in Requisition.objects.filter(state=State.COMPLETED, closed_at__gte=since, submitted_at__isnull=False):
        to_completion[req.kind].append(working_days_between(req.submitted_at, req.closed_at))
    return {
        "to_procurement": _median(to_procurement),
        "to_procurement_samples": len(to_procurement),
        "completion": [
            {
                "kind": kind,
                "label": Kind(kind).label,
                "median": _median(to_completion.get(kind, [])),
                "samples": len(to_completion.get(kind, [])),
            }
            for kind in (Kind.ALLOCATION, Kind.EQUIPMENT, Kind.REPLACEMENT)
        ],
    }


def voucher_figures(since):
    vouchers = IssueVoucher.objects.filter(created_at__gte=since)
    signed = list(vouchers.filter(status=IssueVoucher.Status.ACKNOWLEDGED))
    sign_days = [working_days_between(v.created_at, v.acknowledged_at) for v in signed if v.acknowledged_at]
    return {
        "raised": vouchers.count(),
        "signed": len(signed),
        "disputed": vouchers.filter(status=IssueVoucher.Status.DISPUTED).count(),
        "withdrawn": vouchers.filter(status=IssueVoucher.Status.WITHDRAWN).count(),
        "awaiting": IssueVoucher.objects.filter(status=IssueVoucher.Status.AWAITING).count(),
        "median_days_to_sign": _median(sign_days),
    }


def movement_figures(since):
    counts = Counter(AssetMovement.objects.filter(created_at__gte=since).values_list("kind", flat=True))
    rows = [{"kind": k, "label": MovementKind(k).label, "count": counts.get(k, 0)} for k in MovementKind]
    without_actor = AssetMovement.objects.filter(created_at__gte=since, performed_by__isnull=True).count()
    total = sum(counts.values())
    return {"rows": rows, "total": total, "without_actor": without_actor}


def summary(days=90):
    now = timezone.now()
    since = now - timedelta(days=days)
    open_rows = open_by_stage()
    completed = Requisition.objects.filter(state=State.COMPLETED, closed_at__gte=since).count()
    submitted = Requisition.objects.filter(submitted_at__gte=since).count()
    declined = Requisition.objects.filter(state=State.DECLINED, closed_at__gte=since).count()
    return {
        "days": days,
        "since": since,
        "open_rows": open_rows,
        "open_total": sum(r["count"] for r in open_rows),
        "overdue_total": sum(r["overdue"] for r in open_rows),
        "submitted": submitted,
        "completed": completed,
        "declined": declined,
        "durations": stage_durations(since),
        "turnaround": turnaround(since),
        "vouchers": voucher_figures(since),
        "movements": movement_figures(since),
    }
