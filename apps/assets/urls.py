from django.urls import path

from . import views

app_name = "assets"

urlpatterns = [
    path("", views.asset_list, name="list"),
    path("export.xlsx", views.export_assets, name="export"),
    path("new/", views.asset_create, name="create"),
    path("vouchers/", views.voucher_list, name="vouchers"),
    path("vouchers/<str:number>/", views.voucher_detail, name="voucher_detail"),
    path("vouchers/<str:number>/print/", views.voucher_print, name="voucher_print"),
    path("<str:tag>/", views.asset_detail, name="detail"),
    path("<str:tag>/edit/", views.asset_edit, name="edit"),
    path("<str:tag>/status/", views.asset_status, name="status"),
]
