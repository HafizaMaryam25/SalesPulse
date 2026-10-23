"""REST API (Django REST Framework).

GET  /api/sales-summary/?year=2018     KPIs, top categories, regions, monthly revenue
GET  /api/categories/                  vw_CategorySalesSummary
GET  /api/top-products/?category=&top= sp_GetTopProductsByCategory
CRUD /api/inventory/                   read: any role, write: Analyst+, delete: Admin
"""
from django.forms.models import model_to_dict
from rest_framework import serializers, viewsets
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import services
from .audit import log_action
from .models import AuditLog, InventoryItem
from .roles import InventoryPermission


class InventoryItemSerializer(serializers.ModelSerializer):
    stock_status = serializers.ReadOnlyField()
    stock_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    updated_by = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = InventoryItem
        fields = ["id", "sku", "name", "category", "product_id", "unit_price", "unit_cost",
                  "stock_qty", "reorder_level", "is_active", "stock_status", "stock_value",
                  "updated_at", "updated_by"]


class InventoryViewSet(viewsets.ModelViewSet):
    queryset = InventoryItem.objects.all()
    serializer_class = InventoryItemSerializer
    permission_classes = [InventoryPermission]

    def perform_create(self, serializer):
        item = serializer.save(updated_by=self.request.user)
        log_action(self.request.user, AuditLog.CREATE, f"Added {item.sku} via API",
                   obj=item, request=self.request)

    def perform_update(self, serializer):
        before = model_to_dict(serializer.instance)
        item = serializer.save(updated_by=self.request.user)
        after = model_to_dict(item)
        changes = {k: [str(before[k]), str(after[k])] for k in after if before.get(k) != after[k]}
        log_action(self.request.user, AuditLog.UPDATE, f"Changed {item.sku} via API",
                   obj=item, changes=changes, request=self.request)

    def perform_destroy(self, instance):
        log_action(self.request.user, AuditLog.DELETE, f"Deleted {instance.sku} via API",
                   obj=instance, request=self.request)
        instance.delete()


def _year_param(request):
    try:
        return int(request.query_params.get("year"))
    except (TypeError, ValueError):
        return None


@api_view(["GET"])
def sales_summary(request):
    year = _year_param(request)
    period = services.get_period(year, services.data_range())
    kpi = services.kpi_summary(period.start, period.end)
    if kpi is None:
        return Response({"status": "error", "message": "The warehouse is not reachable."}, status=503)
    return Response({
        "status": "success",
        "period": {"year": period.year, "start": period.start, "end": period.end},
        "currency": "BRL",
        "kpis": kpi,
        "top_categories": services.top_categories(period.start, period.end, 10),
        "regions": services.region_sales(period.start, period.end),
        "monthly": services.monthly_revenue(year),
    })


@api_view(["GET"])
def categories(request):
    return Response({"status": "success", "data": services.category_summary()})


@api_view(["GET"])
def top_products(request):
    try:
        top = max(1, min(int(request.query_params.get("top", 10)), 100))
    except ValueError:
        top = 10
    category = request.query_params.get("category") or None
    return Response({"status": "success", "category": category,
                     "data": services.top_products(category, top)})
