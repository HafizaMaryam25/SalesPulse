from django.contrib.auth import views as auth_views
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import api, views
from .forms import SignInForm

router = DefaultRouter()
router.register("inventory", api.InventoryViewSet, basename="api-inventory")

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", auth_views.LoginView.as_view(authentication_form=SignInForm,
                                                redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    path("inventory/", views.inventory_list, name="inventory_list"),
    path("inventory/new/", views.inventory_create, name="inventory_create"),
    path("inventory/<int:pk>/edit/", views.inventory_update, name="inventory_update"),
    path("inventory/<int:pk>/delete/", views.inventory_delete, name="inventory_delete"),

    path("upload/", views.upload, name="upload"),
    path("audit/", views.audit_log, name="audit_log"),

    path("api/sales-summary/", api.sales_summary, name="api_sales_summary"),
    path("api/categories/", api.categories, name="api_categories"),
    path("api/top-products/", api.top_products, name="api_top_products"),
    path("api/", include(router.urls)),
]
