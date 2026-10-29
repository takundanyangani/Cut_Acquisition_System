import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse


def _is_exempt(path):
    script = settings.FORCE_SCRIPT_NAME or ""
    if script and path.startswith(script):
        path = path[len(script) :]
    return path.startswith(("/static/", "/api/", "/healthz"))


class IdleTimeoutMiddleware:
    """Sign the user out after ACQ_IDLE_TIMEOUT_MINUTES without a request."""

    WRITE_EVERY_SECONDS = 60

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not _is_exempt(request.path):
            now = int(time.time())
            last = request.session.get("last_activity")
            limit = settings.ACQ_IDLE_TIMEOUT_MINUTES * 60
            if last is not None and now - last > limit:
                logout(request)
                messages.info(
                    request,
                    f"You were signed out after {settings.ACQ_IDLE_TIMEOUT_MINUTES} minutes without "
                    "activity. Sign in again to continue.",
                )
                return redirect_to_login(request.get_full_path())
            if last is None or now - last >= self.WRITE_EVERY_SECONDS:
                request.session["last_activity"] = now
        return self.get_response(request)


class ForcePasswordChangeMiddleware:
    """Send users with a temporary password to the change-password page."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and user.must_change_password and not _is_exempt(request.path):
            allowed = {reverse("accounts:password_change"), reverse("accounts:logout")}
            if request.path not in allowed:
                messages.warning(request, "Choose a new password before you continue.")
                return redirect("accounts:password_change")
        return self.get_response(request)
