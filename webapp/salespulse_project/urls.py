from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "SalesPulse admin"
admin.site.site_title = "SalesPulse"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api-auth/", include("rest_framework.urls")),
    path("", include("core.urls")),
]

handler403 = "core.views.permission_denied"
