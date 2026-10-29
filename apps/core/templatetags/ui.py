"""Template helpers for the design system: icons, stamps and form fields."""

from django import template
from django.templatetags.static import static
from django.utils import timezone
from django.utils.html import format_html

from apps.core.workdays import working_days_between

register = template.Library()

STATE_TONES = {
    "draft": ("neutral", "pen-line"),
    "returned": ("warn", "undo-2"),
    "awaiting_ack": ("warn", "signature"),
    "completed": ("ok", "circle-check"),
    "declined": ("danger", "circle-x"),
    "cancelled": ("neutral", "ban"),
    "converted": ("info", "refresh-cw"),
    "with_procurement": ("info", "truck"),
    "approved": ("ok", "stamp"),
}

ACTION_STYLE = {
    "submit": ("info", "send"),
    "pa_forward": ("ok", "arrow-right"),
    "director_forward": ("ok", "stamp"),
    "csm_assign": ("ok", "user-check"),
    "submit_specifications": ("info", "clipboard-check"),
    "ops_confirm": ("ok", "shield-check"),
    "director_approve": ("ok", "stamp"),
    "send_to_procurement": ("info", "send"),
    "csm_recommend": ("ok", "stamp"),
    "csm_not_recommend": ("warn", "stamp"),
    "director_approve_replacement": ("ok", "stamp"),
    "initiate_procurement": ("info", "send"),
    "convert": ("info", "refresh-cw"),
    "created_from_conversion": ("info", "refresh-cw"),
    "issue_from_stock": ("info", "receipt"),
    "raise_voucher": ("info", "receipt"),
    "record_delivery": ("info", "truck"),
    "follow_up": ("neutral", "clock"),
    "procurement_update": ("neutral", "message-square"),
    "acknowledge": ("ok", "signature"),
    "claim": ("neutral", "user-check"),
    "reassign": ("neutral", "user-check"),
    "edited": ("neutral", "pen-line"),
}


@register.simple_tag
def icon(name, extra_class=""):
    href = f"{static('icons/sprite.svg')}#i-{name}"
    css = f"icon {extra_class}".strip()
    return format_html('<svg class="{}" aria-hidden="true" focusable="false"><use href="{}"></use></svg>', css, href)


@register.filter
def state_tone(state):
    return STATE_TONES.get(str(state), ("info", "circle-dot"))[0]


@register.filter
def state_icon(state):
    return STATE_TONES.get(str(state), ("info", "circle-dot"))[1]


@register.filter
def action_tone(action):
    action = str(action)
    if action in ACTION_STYLE:
        return ACTION_STYLE[action][0]
    if "return" in action or action in {"dispute", "withdraw_voucher"}:
        return "warn"
    if "decline" in action or "cancel" in action:
        return "danger"
    return "neutral"


@register.filter
def action_icon(action):
    action = str(action)
    if action in ACTION_STYLE:
        return ACTION_STYLE[action][1]
    if "return" in action or action in {"dispute", "withdraw_voucher"}:
        return "undo-2"
    if "decline" in action or "cancel" in action:
        return "circle-x"
    return "circle-dot"


@register.filter
def button_class(tone):
    return {"primary": "btn-primary", "secondary": "btn-secondary", "danger": "btn-danger-outline"}.get(
        tone, "btn-secondary"
    )


@register.filter
def tone_icon(transition):
    """Icon for an action button, from the transition's code."""
    return action_icon(transition.code)


@register.inclusion_tag("includes/stamp.html")
def state_stamp(req, large=False):
    from apps.requisitions.models import State

    return {
        "label": State(req.state).label,
        "tone": state_tone(req.state),
        "icon": state_icon(req.state),
        "large": large,
    }


@register.inclusion_tag("includes/field.html")
def field(bound_field, label=None, hide_label=False, extra_class=""):
    describedby = []
    if bound_field.help_text:
        describedby.append(f"{bound_field.auto_id}_help")
    if bound_field.errors:
        describedby.append(f"{bound_field.auto_id}_error")
    attrs = {}
    if describedby:
        attrs["aria-describedby"] = " ".join(describedby)
    if bound_field.errors:
        attrs["aria-invalid"] = "true"
    widget_type = bound_field.field.widget.__class__.__name__
    return {
        "f": bound_field,
        "widget": bound_field.as_widget(attrs=attrs),
        "label": label or bound_field.label,
        "hide_label": hide_label,
        "is_check": widget_type == "CheckboxInput",
        "is_multi_check": widget_type == "CheckboxSelectMultiple",
        "extra_class": extra_class,
    }


DETAIL_LABELS = [
    ("technician", "Technician"),
    ("previous_technician", "Previously"),
    ("procurement_reference", "Procurement ref"),
    ("purchase_order", "Purchase order"),
    ("status", "Status"),
    ("expected_delivery_on", "Expected delivery"),
    ("next_follow_up_on", "Next follow-up"),
    ("voucher", "Voucher"),
    ("assets", "Assets"),
    ("assets_logged", "Assets logged"),
    ("new_reference", "New request"),
    ("converted_from", "From"),
    ("fields", "Changed"),
    ("file", "File"),
]


@register.filter
def entry_details(entry):
    data = entry.data or {}
    rows = []
    for key, label in DETAIL_LABELS:
        value = data.get(key)
        if value:
            rows.append((label, value))
    return rows


@register.filter
def holder_name(req):
    from apps.requisitions.workflow import stage_holder

    return stage_holder(req)


@register.filter
def working_days_since(value):
    if not value:
        return ""
    return working_days_between(value, timezone.now())


@register.simple_tag
def due_text(req):
    """'Due 30 Oct', 'Due today' or 'Overdue by 2 working days'."""
    if not req.state_due_at or not req.is_open:
        return ""
    now = timezone.now()
    due = timezone.localtime(req.state_due_at)
    if req.state_due_at < now:
        late = working_days_between(req.state_due_at, now)
        if late <= 0:
            return "Past due, was due at " + due.strftime("%H:%M")
        return f"Overdue by {late} working day{'s' if late != 1 else ''}"
    if due.date() == timezone.localdate():
        return "Due today, " + due.strftime("%H:%M")
    return f"Due {due.day} {due:%b}"


@register.filter
def filesize(value):
    try:
        size = float(value)
    except (TypeError, ValueError):
        return ""
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


@register.simple_tag(takes_context=True)
def nav_current(context, *names):
    """aria-current="page" when the current URL name is one of `names`."""
    request = context.get("request")
    match = getattr(request, "resolver_match", None)
    if match is None:
        return ""
    full = f"{match.namespace}:{match.url_name}" if match.namespace else match.url_name
    return format_html('aria-current="page"') if full in names else ""


@register.filter
def percent_of(value, total):
    try:
        return max(0.0, min(100.0, float(value) * 100.0 / float(total)))
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


@register.filter
def scale(value, args):
    """Map value onto a pixel width: "max,width"."""
    try:
        maximum, width = (float(a) for a in str(args).split(","))
        return round(max(0.0, float(value or 0)) * width / maximum, 1) if maximum else 0
    except (TypeError, ValueError):
        return 0
