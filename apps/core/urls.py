from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/<int:pk>/", views.open_notification, name="open_notification"),
    path("notifications/read-all/", views.mark_all_read, name="mark_all_read"),
    path("process/", views.process_guide, name="process"),
    path("reports/", views.reports_view, name="reports"),
]
