"""
The approval routes of CUT ICT 38, 40 and 42, written as data.

Each Transition names the stage it starts from, the stage it leads to, who
may perform it and the SOP clause it implements. Views, queues, reminders
and the process guide all read this table, so a change to a procedure is a
change here. docs/03-workflows.md prints the same table for auditors.
"""

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.db.models import Q

from apps.accounts.models import Capacity, Delegation, Role

from .models import TERMINAL_STATES, Kind, Requisition, State

REQUESTER = "requester"
TECHNICIAN = "assigned_technician"

ALL = frozenset(Kind)
ALC = frozenset({Kind.ALLOCATION})
EQR = frozenset({Kind.EQUIPMENT})
RPL = frozenset({Kind.REPLACEMENT})
EQR_RPL = EQR | RPL

FIRST_STAGE = {
    Kind.ALLOCATION: State.TECHNICIAN_REVIEW,
    Kind.EQUIPMENT: State.PA_REVIEW,
    Kind.REPLACEMENT: State.CSM_REVIEW,
}

# States in which the requester may still withdraw: nothing has been bought.
REQUESTER_CANCELLABLE = frozenset(
    {
        State.RETURNED,
        State.PA_REVIEW,
        State.DIRECTOR_REVIEW,
        State.CSM_REVIEW,
        State.TECHNICIAN_REVIEW,
        State.OPS_REVIEW,
        State.DIRECTOR_APPROVAL,
    }
)
FULFILMENT_STATES = frozenset(
    {State.TECHNICIAN_REVIEW, State.APPROVED, State.WITH_PROCUREMENT, State.READY_FOR_ISSUE, State.AWAITING_ACK}
)


@dataclass(frozen=True)
class Transition:
    code: str
    label: str  # button and page heading
    done: str  # past tense, for the routing slip
    kinds: frozenset
    sources: frozenset
    target: str | None  # None: stays in the same stage, or the handler decides
    actor: str  # REQUESTER, TECHNICIAN or a Role value
    sop: str = ""
    comment: str = "optional"  # "required", "optional" or "none"
    sign: bool = False  # password re-entry
    tone: str = "primary"  # primary, secondary or danger
    form: str = "decision"  # which form the action page shows
    primary: bool = True  # moves the request forward (feeds queues and notifications)
    independent: bool = False  # the assigned technician may not perform it
    help: str = ""

    @property
    def comment_required(self):
        return self.comment == "required"

    @property
    def changes_state(self):
        return self.target is not None


def _t(*args, **kwargs):
    return Transition(*args, **kwargs)


S = State
# fmt: off
TRANSITIONS = [
    # -- Submission ----------------------------------------------------------
    _t("submit", "Submit request", "Submitted the request", ALC, {S.DRAFT, S.RETURNED}, None, REQUESTER,
       sop="CUT ICT 40 §5.1", help="The Helpdesk checks whether ICT holds the item in stock."),
    _t("submit", "Submit request", "Submitted the request", EQR, {S.DRAFT, S.RETURNED}, None, REQUESTER,
       sop="CUT ICT 38 §5.1",
       help="The request goes to the ICT Director through the Personal Advisor."),
    _t("submit", "Submit request", "Submitted the request", RPL, {S.DRAFT, S.RETURNED}, None, REQUESTER,
       sop="CUT ICT 42 §5.1", help="The Client Services Manager reviews your reasons for replacement."),

    # -- CUT ICT 38: Equipment request ---------------------------------------
    _t("pa_forward", "Forward to the ICT Director", "Forwarded to the ICT Director", EQR, {S.PA_REVIEW},
       S.DIRECTOR_REVIEW, Role.PERSONAL_ADVISOR, sop="CUT ICT 38 §5.1",
       help="Check the request is complete before it reaches the Director."),
    _t("pa_return", "Return to requester", "Returned to the requester", EQR, {S.PA_REVIEW}, S.RETURNED,
       Role.PERSONAL_ADVISOR, comment="required", tone="secondary",
       help="Say what is missing. The request comes back to you when the requester resubmits."),
    _t("director_forward", "Comment and forward to the CSM", "Commented and forwarded to the CSM", EQR,
       {S.DIRECTOR_REVIEW}, S.CSM_REVIEW, Role.ICT_DIRECTOR, sop="CUT ICT 38 §5.2", comment="required",
       sign=True, help="Your comment travels with the request to the Client Services Manager."),
    _t("director_return", "Return to requester", "Returned to the requester", EQR, {S.DIRECTOR_REVIEW},
       S.RETURNED, Role.ICT_DIRECTOR, comment="required", tone="secondary"),
    _t("director_decline", "Decline request", "Declined the request", EQR, {S.DIRECTOR_REVIEW}, S.DECLINED,
       Role.ICT_DIRECTOR, comment="required", sign=True, tone="danger",
       help="Declining closes the request. The requester sees your reason."),
    _t("csm_assign", "Assign a technician", "Assigned a technician", EQR, {S.CSM_REVIEW}, S.TECHNICIAN_REVIEW,
       Role.CLIENT_SERVICES_MANAGER, sop="CUT ICT 38 §5.3", form="assign",
       help="The technician checks stock, or finds specifications and prepares the purchase request."),
    _t("csm_return", "Return to requester", "Returned to the requester", EQR_RPL, {S.CSM_REVIEW}, S.RETURNED,
       Role.CLIENT_SERVICES_MANAGER, comment="required", tone="secondary"),
    _t("submit_specifications", "Submit specifications", "Submitted specifications and purchase request",
       EQR, {S.TECHNICIAN_REVIEW}, S.OPS_REVIEW, TECHNICIAN, sop="CUT ICT 38 §5.4, §5.5", form="specifications",
       help="Write the recommended specification for each item and attach at least one specification sheet."),
    _t("ops_confirm", "Confirm specifications", "Cross-checked and confirmed the specifications", EQR,
       {S.OPS_REVIEW}, S.DIRECTOR_APPROVAL, Role.OPERATIONS_MANAGER, sop="CUT ICT 38 §5.6", sign=True,
       independent=True, help="Confirm the specifications match the requirement before the Director approves."),
    _t("ops_return", "Return to technician", "Returned to the technician", EQR, {S.OPS_REVIEW},
       S.TECHNICIAN_REVIEW, Role.OPERATIONS_MANAGER, comment="required", tone="secondary"),
    _t("director_approve", "Approve purchase request", "Approved the purchase request", EQR,
       {S.DIRECTOR_APPROVAL}, S.APPROVED, Role.ICT_DIRECTOR, sop="CUT ICT 38 §5.6, §5.7", sign=True,
       independent=True,
       help="Approval records the request in the tracking register and releases it to Procurement."),
    _t("director_return_technician", "Return to technician", "Returned to the technician", EQR,
       {S.DIRECTOR_APPROVAL}, S.TECHNICIAN_REVIEW, Role.ICT_DIRECTOR, comment="required", tone="secondary"),
    _t("director_decline", "Decline request", "Declined the request", EQR, {S.DIRECTOR_APPROVAL}, S.DECLINED,
       Role.ICT_DIRECTOR, comment="required", sign=True, tone="danger"),
    _t("send_to_procurement", "Send to Procurement", "Sent to the Procurement Department", EQR, {S.APPROVED},
       S.WITH_PROCUREMENT, TECHNICIAN, sop="CUT ICT 38 §5.8", form="procurement",
       help="Record the requisition number Procurement gave you, so follow-ups can quote it."),

    # -- CUT ICT 42: Replacement ----------------------------------------------
    _t("csm_recommend", "Recommend replacement", "Recommended the replacement", RPL, {S.CSM_REVIEW},
       S.DIRECTOR_APPROVAL, Role.CLIENT_SERVICES_MANAGER, sop="CUT ICT 42 §5.2", sign=True, form="recommend",
       help="Choose the technician who will prepare the specification sheet once the Director approves."),
    _t("csm_not_recommend", "Do not recommend", "Did not recommend the replacement", RPL, {S.CSM_REVIEW},
       S.DIRECTOR_APPROVAL, Role.CLIENT_SERVICES_MANAGER, sop="CUT ICT 42 §5.2", comment="required",
       sign=True, tone="secondary", form="recommend",
       help="The ICT Director makes the final decision and sees your reasons."),
    _t("director_approve_replacement", "Approve replacement", "Approved the replacement", RPL,
       {S.DIRECTOR_APPROVAL}, S.TECHNICIAN_REVIEW, Role.ICT_DIRECTOR, sop="CUT ICT 42 §5.3", sign=True,
       independent=True),
    _t("director_return", "Return to requester", "Returned to the requester", RPL, {S.DIRECTOR_APPROVAL},
       S.RETURNED, Role.ICT_DIRECTOR, comment="required", tone="secondary"),
    _t("director_decline", "Decline replacement", "Declined the replacement", RPL, {S.DIRECTOR_APPROVAL},
       S.DECLINED, Role.ICT_DIRECTOR, comment="required", sign=True, tone="danger"),
    _t("initiate_procurement", "Initiate procurement request", "Initiated the procurement request", RPL,
       {S.TECHNICIAN_REVIEW}, S.WITH_PROCUREMENT, TECHNICIAN, sop="CUT ICT 42 §5.4", form="specifications",
       help="Attach the specification sheet and record the Procurement requisition number."),

    # -- CUT ICT 40: Allocation -------------------------------------------------
    _t("convert", "Not in stock: raise equipment request", "Converted to an equipment request", ALC,
       {S.TECHNICIAN_REVIEW}, S.CONVERTED, TECHNICIAN, sop="CUT ICT 38 §5.1", comment="required",
       tone="secondary",
       help="Creates an equipment request for the same user and items. It goes to the Personal Advisor."),
    _t("technician_return", "Return to requester", "Returned to the requester", ALC, {S.TECHNICIAN_REVIEW},
       S.RETURNED, TECHNICIAN, comment="required", tone="secondary"),

    # -- Fulfilment (all types) --------------------------------------------------
    _t("issue_from_stock", "Issue from stock", "Raised an issue voucher from stock", ALL, {S.TECHNICIAN_REVIEW},
       S.AWAITING_ACK, TECHNICIAN, sop="CUT ICT 40 §5.2", form="voucher",
       help="Choose the assets to hand over. The user signs the voucher to confirm receipt."),
    _t("follow_up", "Log a follow-up", "Followed up with Procurement", EQR_RPL, {S.WITH_PROCUREMENT}, None,
       TECHNICIAN, sop="CUT ICT 38 §5.9", comment="required", form="follow_up", tone="secondary"),
    _t("procurement_update", "Post a procurement update", "Posted a procurement update", EQR_RPL,
       {S.WITH_PROCUREMENT}, None, Role.PROCUREMENT_OFFICER, sop="CUT ICT 42 §5.5", form="procurement_update",
       tone="secondary"),
    _t("record_delivery", "Record delivery", "Recorded delivery and logged equipment", EQR_RPL,
       {S.WITH_PROCUREMENT}, None, TECHNICIAN, sop="CUT ICT 42 §5.5, §5.6", form="delivery",
       help="Log each delivered item in the asset register. Tick the box when the order is complete."),
    _t("raise_voucher", "Raise issue voucher", "Raised an issue voucher", ALL, {S.READY_FOR_ISSUE},
       S.AWAITING_ACK, TECHNICIAN, sop="CUT ICT 40 §5.2", form="voucher"),
    _t("withdraw_voucher", "Withdraw voucher", "Withdrew the issue voucher", ALL, {S.AWAITING_ACK},
       S.READY_FOR_ISSUE, TECHNICIAN, comment="required", tone="secondary", primary=False,
       help="The assets go back to stock and you can raise a corrected voucher."),
    _t("acknowledge", "Sign for receipt", "Signed for receipt", ALL, {S.AWAITING_ACK}, S.COMPLETED, REQUESTER,
       sop="CUT ICT 40 §5.3", sign=True, form="acknowledge",
       help="Check the serial number, specification and condition of each item before you sign."),
    _t("dispute", "Report a problem", "Disputed the issue voucher", ALL, {S.AWAITING_ACK}, S.READY_FOR_ISSUE,
       REQUESTER, sop="CUT ICT 40 §5.3", comment="required", tone="secondary",
       help="Use this if a serial number, specification or condition does not match what you received."),

    # -- Work allocation ----------------------------------------------------------
    _t("claim", "Take this request", "Took the request", ALC, {S.TECHNICIAN_REVIEW}, None, TECHNICIAN,
       comment="none", tone="secondary", primary=False),
    _t("reassign", "Reassign technician", "Reassigned the technician", ALL, FULFILMENT_STATES, None,
       Role.CLIENT_SERVICES_MANAGER, form="assign", tone="secondary", primary=False),

    # -- Closing early -------------------------------------------------------------
    _t("cancel", "Cancel request", "Cancelled the request", ALL, REQUESTER_CANCELLABLE, S.CANCELLED, REQUESTER,
       comment="required", tone="danger", primary=False),
    _t("management_cancel", "Cancel request", "Cancelled the request for ICT management", EQR_RPL,
       {S.APPROVED, S.WITH_PROCUREMENT, S.READY_FOR_ISSUE}, S.CANCELLED, Role.ICT_DIRECTOR, comment="required",
       sign=True, tone="danger", primary=False,
       help="Use when Procurement cannot supply or the need has gone. Delivered assets stay in stock."),
]
# fmt: on

# What a signing officer declares. Shown above the password box and stored
# in the audit entry with the signature.
PLEDGES = {
    "director_forward": "have reviewed this request and forward it to the Client Services Manager with my comments",
    "director_decline": "decline this request for the reason given",
    "ops_confirm": "have cross-checked the specifications against the requirement and confirm them",
    "director_approve": "approve this purchase request for submission to the Procurement Department",
    "csm_recommend": "recommend this replacement to the ICT Director",
    "csm_not_recommend": "do not recommend this replacement, for the reasons given",
    "director_approve_replacement": "approve the replacement of this machine",
    "management_cancel": "cancel this request for ICT management, for the reason given",
    "acknowledge": (
        "confirm that I received the items on the issue voucher and that their serial numbers, "
        "specifications and condition match it"
    ),
}


def signing_statement(requisition, user, transition, cap):
    role = cap.role_label if cap.role != REQUESTER else "the requester"
    if cap.on_behalf_of is not None:
        role += f", acting for {cap.on_behalf_of.display_name}"
    pledge = PLEDGES.get(transition.code, transition.done[0].lower() + transition.done[1:])
    return f"I, {user.display_name}, signing as {role}, {pledge}. Request {requisition.reference}."


ACTION_LABELS = {t.code: t.done for t in TRANSITIONS}
ACTION_LABELS.update(
    {
        "note": "Added a note",
        "edited": "Edited the request",
        "attachment_added": "Added an attachment",
        "attachment_removed": "Removed an attachment",
        "created_from_conversion": "Created from an allocation request",
    }
)


def transitions_for(requisition):
    return [t for t in TRANSITIONS if requisition.kind in t.kinds and requisition.state in t.sources]


def find_transition(requisition, code):
    for t in transitions_for(requisition):
        if t.code == code:
            return t
    return None


def capacity(requisition, user, transition):
    """The capacity `user` would act in, or None if they may not act."""
    if not user.is_authenticated or not user.is_active or requisition.archived_at:
        return None
    if transition.actor == REQUESTER:
        return Capacity(role=REQUESTER) if requisition.requester_id == user.pk else None
    # Separation of duties: nobody handles their own request past submission.
    if requisition.requester_id == user.pk:
        return None
    if transition.independent and requisition.assigned_technician_id == user.pk:
        return None
    if transition.actor == TECHNICIAN:
        if not user.has_role(Role.TECHNICIAN):
            return None
        if requisition.assigned_technician_id == user.pk:
            return Capacity(role=Role.TECHNICIAN)
        if (
            requisition.assigned_technician_id is None
            and requisition.kind == Kind.ALLOCATION
            and requisition.state == State.TECHNICIAN_REVIEW
        ):
            return Capacity(role=Role.TECHNICIAN)
        return None
    return user.capacity_for(transition.actor)


def available_transitions(requisition, user):
    result = []
    for t in transitions_for(requisition):
        if t.code == "claim" and requisition.assigned_technician_id is not None:
            continue
        cap = capacity(requisition, user, t)
        if cap is not None:
            result.append((t, cap))
    return result


def role_holders(role):
    """Active users who can act in `role`, directly or by delegation."""
    User = get_user_model()
    direct = set(User.objects.filter(is_active=True, role_grants__role=role).values_list("pk", flat=True))
    delegated = set()
    for d in Delegation.objects.active().filter(role=role, delegate__is_active=True).select_related("delegator"):
        if d.delegator.is_active and d.delegator.role_grants.filter(role=role).exists():
            delegated.add(d.delegate_id)
    return list(User.objects.filter(pk__in=direct | delegated))


def current_actors(requisition):
    """People the request is waiting on now, for notifications and reminders."""
    if requisition.state in TERMINAL_STATES or requisition.archived_at:
        return []
    people = {}
    for t in transitions_for(requisition):
        if not t.primary:
            continue
        if t.actor == REQUESTER:
            candidates = [requisition.requester]
        elif t.actor == TECHNICIAN:
            if requisition.assigned_technician_id:
                candidates = [requisition.assigned_technician]
            elif requisition.kind == Kind.ALLOCATION:
                candidates = role_holders(Role.TECHNICIAN)
            else:
                candidates = []
        else:
            candidates = role_holders(t.actor)
        for person in candidates:
            if person is None or not person.is_active:
                continue
            if capacity(requisition, person, t) is not None:
                people[person.pk] = person
    return list(people.values())


STAGE_HOLDER = {
    State.DRAFT: "You (draft)",
    State.RETURNED: "Requester",
    State.PA_REVIEW: "Personal Advisor",
    State.DIRECTOR_REVIEW: "ICT Director",
    State.CSM_REVIEW: "Client Services Manager",
    State.OPS_REVIEW: "Operations Manager",
    State.DIRECTOR_APPROVAL: "ICT Director",
    State.AWAITING_ACK: "Requester",
}


def stage_holder(requisition):
    """Plain description of whose desk the request is on."""
    state = requisition.state
    if state in TERMINAL_STATES:
        return "Closed"
    tech = requisition.assigned_technician
    if state in {State.TECHNICIAN_REVIEW, State.APPROVED, State.READY_FOR_ISSUE}:
        if tech:
            return tech.display_name
        return "Helpdesk queue" if requisition.kind == Kind.ALLOCATION else "Unassigned"
    if state == State.WITH_PROCUREMENT:
        return f"Procurement, followed up by {tech.display_name}" if tech else "Procurement"
    if state in {State.RETURNED, State.AWAITING_ACK}:
        return requisition.requester.display_name
    return STAGE_HOLDER.get(state, "")


def blocked_reason(requisition):
    """Explain when nobody is allowed to act, for example when the only
    ICT Director raised the request and no delegation exists."""
    if requisition.state in TERMINAL_STATES or requisition.state == State.DRAFT or requisition.archived_at:
        return ""
    primary = [t for t in transitions_for(requisition) if t.primary]
    if not primary:
        return ""
    if current_actors(requisition):
        return ""
    if requisition.state in {State.TECHNICIAN_REVIEW, State.APPROVED, State.READY_FOR_ISSUE, State.WITH_PROCUREMENT}:
        if requisition.kind != Kind.ALLOCATION or requisition.state != State.TECHNICIAN_REVIEW:
            return "No active technician is assigned. The Client Services Manager can reassign it."
    roles = sorted({t.actor for t in primary if t.actor not in (REQUESTER, TECHNICIAN)})
    if roles:
        names = ", ".join(Role(r).label for r in roles)
        return (
            f"No one can act at this stage. The {names} role is vacant or held only by the requester. "
            "Ask the role holder to set up a delegation, or ask a system administrator to assign the role."
        )
    return "No one can act at this stage. Contact a system administrator."


def queue_filter(user):
    """A database filter for requests that may need `user`. The caller
    confirms each with available_transitions() before showing it."""
    q = Q(requester=user, state__in=[State.RETURNED, State.AWAITING_ACK])
    if user.has_role(Role.TECHNICIAN):
        q |= Q(assigned_technician=user, state__in=FULFILMENT_STATES)
        q |= Q(kind=Kind.ALLOCATION, state=State.TECHNICIAN_REVIEW, assigned_technician__isnull=True)
    for t in TRANSITIONS:
        if t.primary and t.actor not in (REQUESTER, TECHNICIAN) and user.has_role(t.actor):
            q |= Q(kind__in=list(t.kinds), state__in=list(t.sources))
    return q


def queue_for(user):
    candidates = (
        Requisition.objects.active()
        .filter(queue_filter(user))
        .select_related("requester", "assigned_technician", "campus", "department")
        .prefetch_related("items")
        .order_by("state_due_at", "submitted_at")
    )
    result = []
    for requisition in candidates:
        actions = [t for t, _ in available_transitions(requisition, user) if t.primary]
        if not actions:
            continue
        if requisition.state == State.WITH_PROCUREMENT:
            # Waiting on a supplier is not work, unless a follow-up is due or
            # the user is the Procurement Officer who can post an update.
            if not (requisition.follow_up_due or user.has_role(Role.PROCUREMENT_OFFICER)):
                continue
        result.append(requisition)
    return result


# -- Route maps for the progress tracker --------------------------------------

ROUTES = {
    Kind.ALLOCATION: [
        ("submitted", "Submitted", {State.DRAFT, State.RETURNED}),
        ("helpdesk", "Helpdesk stock check", {State.TECHNICIAN_REVIEW}),
        ("voucher", "Issue voucher", {State.READY_FOR_ISSUE, State.AWAITING_ACK}),
        ("received", "Signed for", {State.COMPLETED}),
    ],
    Kind.EQUIPMENT: [
        ("submitted", "Submitted", {State.DRAFT, State.RETURNED}),
        ("pa", "Personal Advisor", {State.PA_REVIEW}),
        ("director", "ICT Director review", {State.DIRECTOR_REVIEW}),
        ("csm", "Client Services Manager", {State.CSM_REVIEW}),
        ("specs", "Technician specifications", {State.TECHNICIAN_REVIEW}),
        ("ops", "Operations Manager", {State.OPS_REVIEW}),
        ("approval", "ICT Director approval", {State.DIRECTOR_APPROVAL}),
        ("procurement", "Procurement", {State.APPROVED, State.WITH_PROCUREMENT}),
        ("voucher", "Issue voucher", {State.READY_FOR_ISSUE, State.AWAITING_ACK}),
        ("received", "Signed for", {State.COMPLETED}),
    ],
    Kind.REPLACEMENT: [
        ("submitted", "Submitted", {State.DRAFT, State.RETURNED}),
        ("csm", "CSM recommendation", {State.CSM_REVIEW}),
        ("approval", "ICT Director approval", {State.DIRECTOR_APPROVAL}),
        ("specs", "Specification sheet", {State.TECHNICIAN_REVIEW}),
        ("procurement", "Procurement", {State.WITH_PROCUREMENT}),
        ("voucher", "Issue voucher", {State.READY_FOR_ISSUE, State.AWAITING_ACK}),
        ("received", "Signed for", {State.COMPLETED}),
    ],
}


def route_map(requisition):
    """Milestones with status done, current, upcoming or skipped."""
    steps = ROUTES[Kind(requisition.kind)]
    state = requisition.state
    stock = requisition.fulfilment_route == "stock"
    lookup_state = requisition.returned_from_state if state == State.RETURNED else state
    current_index = None
    for index, (_, _, states) in enumerate(steps):
        if lookup_state in states:
            current_index = index
            break
    if current_index is None and state == State.RETURNED:
        current_index = 0
    if state == State.COMPLETED:
        current_index = len(steps)  # everything done
    if current_index is None and state in TERMINAL_STATES:
        # Closed early: mark the furthest stage reached from the audit trail.
        reached = set(requisition.audit_entries.values_list("from_state", flat=True))
        current_index = 0
        for index, (_, _, states) in enumerate(steps):
            if reached & {str(s) for s in states}:
                current_index = index
    milestones = []
    for index, (key, label, _) in enumerate(steps):
        if stock and requisition.kind == Kind.EQUIPMENT and key in {"ops", "approval", "procurement"}:
            status = "skipped"
        elif stock and requisition.kind == Kind.REPLACEMENT and key == "procurement":
            status = "skipped"
        elif index < current_index:
            status = "done"
        elif index == current_index and state not in TERMINAL_STATES:
            status = "current" if state != State.RETURNED else "returned"
        elif index == current_index:
            status = "stopped"
        else:
            status = "upcoming"
        milestones.append({"key": key, "label": label, "status": status})
    return milestones
