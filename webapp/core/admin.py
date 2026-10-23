from django.contrib import admin

from .audit import log_action
from .models import AuditLog, DataUpload, InventoryItem


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "category", "unit_price", "stock_qty", "reorder_level", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("sku", "name", "product_id")

    # Edits made here go to the audit log too, the same as edits in the app and the API.
    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        if change:
            changes = {name: [str(form.initial.get(name)), str(form.cleaned_data.get(name))]
                       for name in form.changed_data}
            log_action(request.user, AuditLog.UPDATE, f"Changed {obj.sku} in Django admin",
                       obj=obj, changes=changes, request=request)
        else:
            log_action(request.user, AuditLog.CREATE, f"Added {obj.sku} in Django admin",
                       obj=obj, request=request)

    def delete_model(self, request, obj):
        log_action(request.user, AuditLog.DELETE, f"Deleted {obj.sku} in Django admin",
                   obj=obj, request=request)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            log_action(request.user, AuditLog.DELETE, f"Deleted {obj.sku} in Django admin",
                       obj=obj, request=request)
        super().delete_queryset(request, queryset)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "username", "action", "object_type", "object_id", "description")
    list_filter = ("action",)
    search_fields = ("username", "description")

    # The audit log is append-only, even for superusers
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DataUpload)
class DataUploadAdmin(admin.ModelAdmin):
    list_display = ("created_at", "original_name", "dataset", "status", "rows_read", "rows_rejected", "user")
    list_filter = ("status", "dataset")
