from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import connection
from django.http import HttpResponseRedirect, HttpResponseServerError, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template import loader
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.assets.models import AssetStatus
from apps.requisitions import reports, workflow
from apps.requisitions.models import OPEN_STATES, Kind, Requisition, State

from .models import Notification


def _greeting():
    hour = timezone.localtime().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


@login_required
def dashboard(request):
    user = request.user
    mine = (
        Requisition.objects.filter(requester=user)
        .active()
        .exclude(state__in=[State.CANCELLED])
        .select_related("assigned_technician")
        .prefetch_related("items")
        .order_by("-updated_at")
    )
    in_progress = [r for r in mine if r.state not in {State.COMPLETED, State.DECLINED, State.CONVERTED}][:6]
    recent_closed = [r for r in mine if r.state in {State.COMPLETED, State.DECLINED, State.CONVERTED}][:3]
    queue = workflow.queue_for(user)
    context = {
        "greeting": _greeting(),
        "queue": queue[:8],
        "queue_total": len(queue),
        "in_progress": in_progress,
        "recent_closed": recent_closed,
        "my_assets": user.assets_held.filter(status=AssetStatus.ALLOCATED).select_related("category")[:6],
        "follow_ups": [],
        "overview": None,
    }
    if user.has_role(Role.TECHNICIAN):
        context["follow_ups"] = [
            r
            for r in Requisition.objects.active()
            .filter(state=State.WITH_PROCUREMENT, assigned_technician=user)
            .order_by("next_follow_up_on")
        ]
    if user.can_view_reports or user.is_ict_staff:
        now = timezone.now()
        open_qs = Requisition.objects.active().filter(state__in=OPEN_STATES)
        since = now - timedelta(days=30)
        context["overview"] = {
            "open": open_qs.count(),
            "overdue": open_qs.filter(state_due_at__lt=now).count(),
            "with_procurement": open_qs.filter(state=State.WITH_PROCUREMENT).count(),
            "completed_30": Requisition.objects.filter(state=State.COMPLETED, closed_at__gte=since).count(),
            "awaiting_signature": open_qs.filter(state=State.AWAITING_ACK).count(),
        }
    return render(request, "core/dashboard.html", context)


@login_required
def notifications(request):
    queryset = request.user.notifications.all()
    page = Paginator(queryset, 30).get_page(request.GET.get("page"))
    return render(request, "core/notifications.html", {"page": page})


@login_required
def open_notification(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    target = notification.link or "/"
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = "/"
    return HttpResponseRedirect(target)


@login_required
@require_POST
def mark_all_read(request):
    request.user.notifications.filter(read_at__isnull=True).update(read_at=timezone.now())
    return redirect("core:notifications")


@login_required
def process_guide(request):
    from apps.requisitions.models import KIND_PROCEDURE

    routes = []
    for kind in (Kind.ALLOCATION, Kind.EQUIPMENT, Kind.REPLACEMENT):
        steps = [t for t in workflow.TRANSITIONS if kind in t.kinds and t.primary and t.sop and t.code != "submit"]
        submit = next(t for t in workflow.TRANSITIONS if t.code == "submit" and kind in t.kinds)
        routes.append(
            {
                "kind": kind,
                "procedure": KIND_PROCEDURE[kind],
                "milestones": [label for _, label, _ in workflow.ROUTES[kind]],
                "steps": [submit] + steps,
            }
        )
    return render(
        request,
        "core/process.html",
        {
            "routes": routes,
            "target_rows": [(State(k).label, v) for k, v in settings.ACQ_STAGE_TARGET_DAYS.items()],
        },
    )


@login_required
def reports_view(request):
    if not request.user.can_view_reports:
        raise PermissionDenied
    try:
        days = int(request.GET.get("days", 90))
    except ValueError:
        days = 90
    if days not in (30, 90, 180, 365):
        days = 90
    data = reports.summary(days)
    max_open = max([r["count"] for r in data["open_rows"]] + [1])
    durations = data["durations"]
    max_duration = max([r["median"] or 0 for r in durations] + [r["target"] or 0 for r in durations] + [1])
    return render(
        request,
        "core/reports.html",
        {"data": data, "max_open": max_open, "max_duration": max_duration, "period_choices": (30, 90, 180, 365)},
    )


def health(request):
    """Liveness check for monitoring. Touches the database, reveals nothing."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        status = "ok"
    except Exception:  # pragma: no cover - only when the database is down
        status = "database unavailable"
    return JsonResponse({"status": status}, status=200 if status == "ok" else 503)


# -- Error pages -----------------------------------------------------------------


def page_not_found(request, exception=None):
    return render(request, "errors/404.html", status=404)


def permission_denied(request, exception=None):
    return render(request, "errors/403.html", status=403)


def bad_request(request, exception=None):
    return render(request, "errors/400.html", status=400)


def server_error(request):
    # Rendered without a request context: the database may be the problem.
    template = loader.get_template("errors/500.html")
    return HttpResponseServerError(template.render({"helpdesk": settings.ACQ_HELPDESK_CONTACT}))


def csrf_failure(request, reason=""):
    return render(request, "errors/csrf.html", status=403)
