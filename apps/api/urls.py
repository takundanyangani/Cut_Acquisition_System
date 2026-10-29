from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("requests/", views.requisition_list, name="requests"),
    path("requests/<str:reference>/", views.requisition_detail, name="request_detail"),
    path("assets/", views.asset_list, name="assets"),
    path("assets/<str:tag>/", views.asset_detail, name="asset_detail"),
    path("vouchers/<str:number>/", views.voucher_detail, name="voucher_detail"),
]
