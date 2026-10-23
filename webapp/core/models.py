from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from etl.validators import DATASETS

DATASET_CHOICES = [(key, spec.label) for key, spec in DATASETS.items()]


class InventoryItem(models.Model):
    """A product the business stocks. Olist has no stock data, so this table is
    owned by the web app (Django creates it) rather than by the ETL."""

    sku = models.CharField("SKU", max_length=40, unique=True)
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=100, db_index=True)
    product_id = models.CharField(
        "Olist product ID", max_length=32, blank=True,
        help_text="Optional link to DimProduct.product_id in the warehouse.")
    unit_price = models.DecimalField(max_digits=10, decimal_places=2,
                                     validators=[MinValueValidator(Decimal("0"))])
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2,
                                    validators=[MinValueValidator(Decimal("0"))])
    stock_qty = models.PositiveIntegerField("Stock on hand", default=0)
    reorder_level = models.PositiveIntegerField(default=10)
    is_active = models.BooleanField("Active", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.sku} - {self.name}"

    @property
    def stock_status(self):
        if self.stock_qty == 0:
            return "out"
        if self.stock_qty <= self.reorder_level:
            return "low"
        return "ok"

    @property
    def stock_value(self):
        return self.stock_qty * self.unit_cost

    @property
    def margin_pct(self):
        if not self.unit_price:
            return None
        return (self.unit_price - self.unit_cost) / self.unit_price * 100


class AuditLog(models.Model):
    LOGIN, LOGOUT, LOGIN_FAILED = "login", "logout", "login_failed"
    CREATE, UPDATE, DELETE = "create", "update", "delete"
    UPLOAD, UPLOAD_REJECTED = "upload", "upload_rejected"
    ETL_SUCCESS, ETL_FAILED = "etl_success", "etl_failed"
    ACTION_CHOICES = [
        (LOGIN, "Signed in"), (LOGOUT, "Signed out"), (LOGIN_FAILED, "Failed sign-in"),
        (CREATE, "Created"), (UPDATE, "Updated"), (DELETE, "Deleted"),
        (UPLOAD, "Uploaded file"), (UPLOAD_REJECTED, "Upload rejected"),
        (ETL_SUCCESS, "ETL succeeded"), (ETL_FAILED, "ETL failed"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="audit_entries")
    username = models.CharField(max_length=150, blank=True)   # kept if the user is deleted
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, db_index=True)
    object_type = models.CharField(max_length=50, blank=True)
    object_id = models.CharField(max_length=50, blank=True)
    description = models.CharField(max_length=500, blank=True)
    changes = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.username} {self.action}"


class DataUpload(models.Model):
    PENDING, RUNNING, SUCCESS, FAILED, REJECTED = "pending", "running", "success", "failed", "rejected"
    STATUS_CHOICES = [(PENDING, "Pending"), (RUNNING, "Running"), (SUCCESS, "Loaded"),
                      (FAILED, "ETL failed"), (REJECTED, "Rejected")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    dataset = models.CharField(max_length=30, choices=DATASET_CHOICES)
    original_name = models.CharField(max_length=255)
    stored_as = models.CharField(max_length=255, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    rows_read = models.IntegerField(null=True, blank=True)
    rows_rejected = models.IntegerField(null=True, blank=True)
    rows_loaded = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    message = models.TextField(blank=True)
    checks = models.JSONField(null=True, blank=True)
    etl_run_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.original_name} ({self.get_status_display()})"


class EtlRunLog(models.Model):
    """Read-only view of the EtlRunLog table that the ETL writes (see sql/01_create_schema.sql)."""

    run_id = models.AutoField(primary_key=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=20)
    triggered_by = models.CharField(max_length=150)
    source = models.CharField(max_length=200, null=True)
    rows_read = models.IntegerField(null=True)
    rows_loaded = models.IntegerField(null=True)
    rows_rejected = models.IntegerField(null=True)
    error_message = models.TextField(null=True)
    details = models.TextField(null=True)

    class Meta:
        managed = False          # Django never creates or alters this table
        db_table = "EtlRunLog"
        ordering = ["-run_id"]
