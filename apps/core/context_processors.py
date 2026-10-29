from django.conf import settings


def app_context(request):
    context = {
        "ORG_NAME": settings.ACQ_ORG_NAME,
        "ORG_SHORT_NAME": settings.ACQ_ORG_SHORT_NAME,
        "UNIT_NAME": settings.ACQ_UNIT_NAME,
        "HELPDESK_CONTACT": settings.ACQ_HELPDESK_CONTACT,
        "CURRENCY": settings.ACQ_CURRENCY,
    }
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return context
    from apps.accounts.models import DELEGABLE_ROLES, Role

    if not hasattr(request, "_nav_cache"):
        from apps.requisitions.workflow import queue_for

        request._nav_cache = {
            "unread_notifications": user.notifications.filter(read_at__isnull=True).count(),
            "queue_count": len(queue_for(user)),
        }
    context.update(request._nav_cache)
    context.update(
        {
            "nav": {
                "all_requests": user.is_ict_staff or user.has_role(Role.PROCUREMENT_OFFICER),
                "assets": user.is_ict_staff,
                "reports": user.can_view_reports,
                "archive": user.has_any_role(
                    {Role.AUDITOR, Role.SYSTEM_ADMIN, Role.CLIENT_SERVICES_MANAGER, Role.ICT_DIRECTOR}
                ),
                "delegations": user.is_system_admin
                or bool(set(user.direct_roles) & DELEGABLE_ROLES)
                or bool(user.delegated_roles),
                "admin": user.is_superuser,
            },
            "acting_roles": [(Role(r).label, person) for r, person in user.delegated_roles.items()],
        }
    )
    return context
