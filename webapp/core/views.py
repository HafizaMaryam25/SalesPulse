from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import F, Q, Sum
from django.forms.models import model_to_dict
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from etl.validators import DATASETS, validate_file

from . import etl_runner, services
from .audit import diff, log_action
from .forms import InventoryItemForm, UploadForm
from .models import AuditLog, DataUpload, InventoryItem
from .roles import can, capability_required


# ------------------------------------------------------------------ dashboard

def _delta(current, previous, kind="pct"):
    """Change badge data. kind='pct' -> relative %, 'pp' -> percentage points, 'abs' -> plain."""
    if previous is None or current is None:
        return None
    if kind == "pct":
        value = services.pct_change(current, previous)
    else:
        value = current - previous
    if value is None:
        return None
    return {"value": value, "up": value >= 0, "kind": kind}


SHORT_DELAY = {1: "8+ early", 2: "1-7 early", 3: "On time", 4: "1-3 late", 5: "4-7 late", 6: "8+ late"}


def _month_label(value):
    return value.strftime("%b %Y") if hasattr(value, "strftime") else str(value)


@login_required
def dashboard(request):
    years = services.available_years()
    try:
        year = int(request.GET.get("year", ""))
        if year not in years:
            year = None
    except ValueError:
        year = None

    period = services.get_period(year, services.data_range())
    kpi = services.kpi_summary(period.start, period.end)
    prev = services.kpi_summary(period.prev_start, period.prev_end) if period.prev_start else None

    def d(key, kind="pct"):
        return _delta(kpi.get(key) if kpi else None, prev.get(key) if prev else None, kind)

    deltas = {
        "revenue": d("total_revenue"),
        "orders": d("total_orders"),
        "aov": d("avg_order_value"),
        "customers": d("total_customers"),
        "repeat": d("repeat_rate_pct", "pp"),
        "late": d("late_delivery_rate_pct", "pp"),
        "review": d("avg_review_score", "abs"),
        "delivery_days": d("avg_delivery_days", "abs"),
    }

    monthly, partial_months = services.split_complete_months(services.monthly_revenue(year))
    delivery, _ = services.split_complete_months(
        [dict(r, orders=r.get("delivered_orders")) for r in services.delivery_trend(year)])
    categories = services.top_categories(period.start, period.end, 8)
    regions = services.region_sales(period.start, period.end)
    region_total = sum(r["revenue"] or 0 for r in regions) or 1
    for r in regions:
        r["share_pct"] = (r["revenue"] or 0) / region_total * 100

    chart_data = {
        "monthly": [{"label": _month_label(r["month_start"]),
                     "revenue": r["revenue"], "orders": r["orders"],
                     "mom": r.get("mom_growth_pct")} for r in monthly],
        "late": [{"label": _month_label(r["month_start"]), "value": r["late_rate_pct"]}
                 for r in delivery],
        "review_delay": [{"label": r["delay_bucket"], "short": SHORT_DELAY.get(r["bucket_order"], ""),
                          "value": r["avg_review_score"], "orders": r["orders"]}
                         for r in services.review_vs_delay()],
    }

    context = {
        "years": years,
        "year": year,
        "period": period,
        "kpi": kpi,
        "deltas": deltas,
        "categories": categories,
        "regions": regions,
        "partial_months": partial_months,
        "chart_data": chart_data,
        "etl_runs": services.recent_etl_runs(5) if can(request.user, "view_etl") else [],
        "warehouse_ok": kpi is not None,
    }
    return render(request, "core/dashboard.html", context)


# ------------------------------------------------------------------ inventory

STATUS_FILTERS = {
    "ok": Q(stock_qty__gt=F("reorder_level")),
    "low": Q(stock_qty__gt=0, stock_qty__lte=F("reorder_level")),
    "out": Q(stock_qty=0),
}


@capability_required("view_inventory")
def inventory_list(request):
    items = InventoryItem.objects.all()
    q = request.GET.get("q", "").strip()
    category = request.GET.get("category", "")
    status = request.GET.get("status", "")
    if q:
        items = items.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(product_id__icontains=q))
    if category:
        items = items.filter(category=category)
    if status in STATUS_FILTERS:
        items = items.filter(STATUS_FILTERS[status])

    sort = request.GET.get("sort", "name")
    allowed_sorts = {"name", "-name", "stock_qty", "-stock_qty", "unit_price", "-unit_price", "-updated_at"}
    items = items.order_by(sort if sort in allowed_sorts else "name")

    all_items = InventoryItem.objects.all()
    summary = {
        "skus": all_items.count(),
        "low": all_items.filter(STATUS_FILTERS["low"]).count(),
        "out": all_items.filter(STATUS_FILTERS["out"]).count(),
        "stock_value": all_items.aggregate(v=Sum(F("stock_qty") * F("unit_cost")))["v"] or 0,
    }

    page = Paginator(items, 15).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "core/inventory_list.html", {
        "page": page,
        "summary": summary,
        "categories": InventoryItem.objects.order_by("category").values_list("category", flat=True).distinct(),
        "filters": {"q": q, "category": category, "status": status, "sort": sort},
        "querystring": params.urlencode(),
    })


def _category_options():
    names = set(InventoryItem.objects.values_list("category", flat=True))
    names.update(services.warehouse_categories())
    return sorted(n for n in names if n)


@capability_required("edit_inventory")
def inventory_create(request):
    form = InventoryItemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.updated_by = request.user
        item.save()
        log_action(request.user, AuditLog.CREATE, f"Added {item.sku} ({item.name})", obj=item,
                   changes={k: [None, str(v)] for k, v in form.cleaned_data.items()}, request=request)
        messages.success(request, f"{item.sku} added to inventory.")
        return redirect("inventory_list")
    return render(request, "core/inventory_form.html",
                  {"form": form, "is_new": True, "category_options": _category_options()})


@capability_required("edit_inventory")
def inventory_update(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    before = model_to_dict(item)
    form = InventoryItemForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        changes = diff(before, form)
        item = form.save(commit=False)
        item.updated_by = request.user
        item.save()
        if changes:
            log_action(request.user, AuditLog.UPDATE,
                       f"Changed {', '.join(changes)} on {item.sku}", obj=item,
                       changes=changes, request=request)
            messages.success(request, f"{item.sku} saved.")
        else:
            messages.info(request, "Nothing changed.")
        return redirect("inventory_list")
    return render(request, "core/inventory_form.html",
                  {"form": form, "item": item, "is_new": False, "category_options": _category_options()})


@capability_required("delete_inventory")
def inventory_delete(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    if request.method == "POST":
        label = f"{item.sku} ({item.name})"
        snapshot = {k: [str(v), None] for k, v in model_to_dict(item).items() if k != "id"}
        log_action(request.user, AuditLog.DELETE, f"Deleted {label}", obj=item,
                   changes=snapshot, request=request)
        item.delete()
        messages.success(request, f"{label} deleted.")
        return redirect("inventory_list")
    return render(request, "core/inventory_confirm_delete.html", {"item": item})


# ------------------------------------------------------------------ upload

@capability_required("upload_data")
def upload(request):
    form = UploadForm(request.POST or None, request.FILES or None)

    if request.method == "POST" and form.is_valid():
        if etl_runner.is_running():
            messages.error(request, "A load is already running. Wait for it to finish, then upload again.")
            return redirect("upload")

        dataset = form.cleaned_data["dataset"]
        spec = form.spec
        f = form.cleaned_data["file"]
        result = validate_file(dataset, f)

        record = DataUpload.objects.create(
            user=request.user, dataset=dataset, original_name=f.name, size_bytes=f.size,
            rows_read=result.rows_read, rows_rejected=result.rows_rejected, checks=result.checks)

        if not result.ok:
            record.status = DataUpload.REJECTED
            record.message = " ".join(result.errors)
            record.finished_at = timezone.now()
            record.save()
            log_action(request.user, AuditLog.UPLOAD_REJECTED,
                       f"{f.name} rejected: {record.message}", obj=record, request=request)
            messages.error(request, f"{f.name} was not loaded. {record.message}")
            return redirect("upload")

        stored = etl_runner.store_upload(f, spec)
        record.stored_as = stored.name
        record.status = DataUpload.RUNNING
        record.message = "Validated. Loading into SQL Server."
        record.save()
        log_action(request.user, AuditLog.UPLOAD,
                   f"Uploaded {f.name} as {spec.label}: {result.rows_read:,} rows, "
                   f"{len(result.checks)} data-quality finding(s)", obj=record, request=request)
        etl_runner.start(record.pk)
        messages.success(request, f"{f.name} passed validation. The ETL is loading it now.")
        return redirect("upload")

    uploads = DataUpload.objects.select_related("user")[:20]
    return render(request, "core/upload.html", {
        "form": form,
        "uploads": uploads,
        "datasets": DATASETS,
        "any_running": any(u.status == DataUpload.RUNNING for u in uploads),
        "max_upload_mb": settings.MAX_UPLOAD_MB,
    })


# ------------------------------------------------------------------ audit log

@capability_required("view_audit")
def audit_log(request):
    entries = AuditLog.objects.select_related("user")
    action = request.GET.get("action", "")
    who = request.GET.get("user", "").strip()
    if action:
        entries = entries.filter(action=action)
    if who:
        entries = entries.filter(username__icontains=who)
    page = Paginator(entries, 25).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "core/audit_log.html", {
        "page": page,
        "actions": AuditLog.ACTION_CHOICES,
        "filters": {"action": action, "user": who},
        "querystring": params.urlencode(),
    })


# ------------------------------------------------------------------ errors

def permission_denied(request, exception=None):
    return render(request, "403.html", {"reason": str(exception or "")}, status=403)
