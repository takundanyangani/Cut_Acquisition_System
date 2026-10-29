from django.urls import path

from . import views

app_name = "requisitions"

urlpatterns = [
    path("", views.my_requests, name="mine"),
    path("queue/", views.queue, name="queue"),
    path("all/", views.all_requests, name="all"),
    path("archive/", views.archive, name="archive"),
    path("export.xlsx", views.export_requests, name="export"),
    path("new/", views.choose_kind, name="choose_kind"),
    path("new/<slug:kind_slug>/", views.create, name="create"),
    path("search/", views.search, name="search"),
    path("ref/<str:reference>/", views.by_reference, name="by_reference"),
    path("files/<uuid:public_id>/", views.download_attachment, name="attachment"),
    path("files/<uuid:public_id>/remove/", views.remove_attachment, name="remove_attachment"),
    path("<uuid:public_id>/", views.detail, name="detail"),
    path("<uuid:public_id>/edit/", views.edit, name="edit"),
    path("<uuid:public_id>/discard/", views.discard, name="discard"),
    path("<uuid:public_id>/print/", views.print_record, name="print"),
    path("<uuid:public_id>/notes/", views.add_note, name="add_note"),
    path("<uuid:public_id>/attachments/", views.upload, name="upload"),
    path("<uuid:public_id>/act/<slug:code>/", views.action, name="action"),
]
