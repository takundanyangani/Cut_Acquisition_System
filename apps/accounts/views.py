from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import DelegationForm, LoginForm, ProfileForm, StyledPasswordChangeForm, StyledSetPasswordForm
from .models import DELEGABLE_ROLES, Delegation


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    pass


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "accounts/password_change.html"
    form_class = StyledPasswordChangeForm
    success_url = reverse_lazy("core:dashboard")

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Your password has been changed.")
        return response


class PasswordResetView(auth_views.PasswordResetView):
    template_name = "accounts/password_reset.html"
    email_template_name = "emails/password_reset.txt"
    subject_template_name = "emails/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def form_valid(self, form):
        self.extra_email_context = {
            "site_url": settings.ACQ_SITE_URL,
            "unit_name": settings.ACQ_UNIT_NAME,
        }
        return super().form_valid(form)


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    form_class = StyledSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


@login_required
def profile(request):
    user = request.user
    if request.method == "POST":
        form = ProfileForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, "Your location details have been saved.")
            return redirect("accounts:profile")
    else:
        form = ProfileForm(instance=user)
    assets = user.assets_held.select_related("category").order_by("asset_tag")
    return render(
        request,
        "accounts/profile.html",
        {
            "form": form,
            "assets": assets,
            "delegations_received": Delegation.objects.active().filter(delegate=user).select_related("delegator"),
        },
    )


def _can_manage_delegations(user):
    return user.is_system_admin or bool(set(user.direct_roles) & DELEGABLE_ROLES)


@login_required
def delegation_list(request):
    user = request.user
    if not _can_manage_delegations(user) and not user.delegated_roles:
        raise PermissionDenied
    if user.is_system_admin:
        delegations = Delegation.objects.all()
    else:
        delegations = Delegation.objects.filter(Q(delegator=user) | Q(delegate=user))
    delegations = delegations.select_related("delegator", "delegate", "created_by").order_by("-starts_at")[:200]
    return render(
        request,
        "accounts/delegation_list.html",
        {"delegations": delegations, "can_create": bool(set(user.direct_roles) & DELEGABLE_ROLES)},
    )


@login_required
def delegation_create(request):
    user = request.user
    if not set(user.direct_roles) & DELEGABLE_ROLES:
        raise PermissionDenied
    form = DelegationForm(request.POST or None, delegator=user, created_by=user)
    if request.method == "POST" and form.is_valid():
        delegation = form.save()
        from apps.core.notify import notify

        notify(
            [delegation.delegate],
            f"You are acting {delegation.get_role_display()} for {user.display_name}",
            (
                f"From {timezone.localtime(delegation.starts_at):%d %b %Y %H:%M} "
                f"until {timezone.localtime(delegation.ends_at):%d %b %Y %H:%M}. "
                f"Reason: {delegation.reason}. Requests at the {delegation.get_role_display()} "
                "stage will appear in your queue and you will sign for the role holder."
            ),
            link=str(reverse_lazy("requisitions:queue")),
        )
        messages.success(
            request,
            f"{delegation.delegate.display_name} will act as {delegation.get_role_display()} for you.",
        )
        return redirect("accounts:delegations")
    return render(request, "accounts/delegation_form.html", {"form": form})


@login_required
@require_POST
def delegation_revoke(request, pk):
    delegation = get_object_or_404(Delegation, pk=pk)
    user = request.user
    if delegation.delegator_id != user.pk and not user.is_system_admin:
        raise PermissionDenied
    if delegation.revoked_at is None and delegation.ends_at > timezone.now():
        delegation.revoked_at = timezone.now()
        delegation.revoked_by = user
        delegation.save(update_fields=["revoked_at", "revoked_by"])
        messages.success(request, "The delegation has been ended.")
    return redirect("accounts:delegations")
