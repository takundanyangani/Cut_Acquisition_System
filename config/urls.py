from django.contrib import admin
from django.urls import include, path

from apps.core import views as core_views

urlpatterns = [
    path("", include("apps.core.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("requests/", include("apps.requisitions.urls")),
    path("assets/", include("apps.assets.urls")),
    path("api/v1/", include("apps.api.urls")),
    path("admin/", admin.site.urls),
    path("healthz", core_views.health, name="health"),
]

handler400 = "apps.core.views.bad_request"
handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.page_not_found"
handler500 = "apps.core.views.server_error"
