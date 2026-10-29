from django.conf import settings

STRICT_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
# The Django admin ships a few inline styles, so its pages get a looser policy.
ADMIN_CSP = STRICT_CSP.replace("style-src 'self'", "style-src 'self' 'unsafe-inline'")


class SecurityHeadersMiddleware:
    """Content Security Policy and related headers on every response."""

    def __init__(self, get_response):
        self.get_response = get_response
        prefix = (settings.FORCE_SCRIPT_NAME or "").rstrip("/")
        self.admin_prefix = f"{prefix}/admin/"

    def __call__(self, request):
        response = self.get_response(request)
        is_admin = request.path.startswith(self.admin_prefix)
        response.headers.setdefault("Content-Security-Policy", ADMIN_CSP if is_admin else STRICT_CSP)
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            response.headers.setdefault("Cache-Control", "private, no-cache")
        return response
