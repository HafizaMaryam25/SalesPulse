"""All reads from the SQL Server warehouse (views + stored procedures).

Every function returns plain Python data and never raises on a database
error: it logs the problem and returns an empty result, so a page still
renders (with an "warehouse not reachable" notice) instead of a 500 error.
"""
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import DatabaseError, connection

logger = logging.getLogger("core")

INCOMPLETE_MONTH_ORDERS = 100   # months below this are export cut-offs, not real dips


class WarehouseError(Exception):
    pass


def _clean(value):
    return float(value) if isinstance(value, Decimal) else value


def fetch(sql, params=None):
    """Run SQL and return a list of dicts. Raises WarehouseError on failure."""
    try:
        with connection.cursor() as cur:
            cur.execute(sql, params or [])
            if cur.description is None:
                return []
            cols = [c[0] for c in cur.description]
            return [{k: _clean(v) for k, v in zip(cols, row)} for row in cur.fetchall()]
    except DatabaseError as exc:
        logger.warning("Warehouse query failed: %s", exc)
        raise WarehouseError(str(exc)) from exc


def call_procedure(name, **params):
    """EXEC dbo.<name> @Param = %s, ...  (values are passed as query parameters)."""
    assignments = ", ".join(f"@{key} = %s" for key in params)
    return fetch(f"EXEC dbo.{name} {assignments}".strip(), list(params.values()))


def safe(default):
    """Decorator: return `default` instead of raising when the warehouse is down."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except WarehouseError:
                return default() if callable(default) else default
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator


# ------------------------------------------------------------------ periods

@dataclass
class Period:
    year: int | None
    start: date | None
    end: date | None
    prev_start: date | None = None
    prev_end: date | None = None

    @property
    def label(self):
        if self.year is None:
            return "All time"
        return f"{self.start:%b %d} – {self.end:%b %d, %Y}"

    @property
    def compare_label(self):
        if not self.prev_start:
            return ""
        return f"vs {self.prev_start:%b %d} – {self.prev_end:%b %d, %Y}"


def _shift_year(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:            # 29 Feb
        return d.replace(year=d.year + years, day=28)


@safe(default=lambda: {"first": None, "last": None})
def data_range():
    rows = fetch("""
        SELECT MIN(d.[date]) AS first_date, MAX(d.[date]) AS last_date
        FROM dbo.FactSales f JOIN dbo.DimDate d ON d.date_key = f.date_key""")
    row = rows[0] if rows else {}
    return {"first": row.get("first_date"), "last": row.get("last_date")}


def get_period(year, rng):
    """Year filter -> date range, compared with the same days one year earlier.

    The comparison window is clipped to the data, so a partial year (2018 ends
    in October) is compared with the same months of 2017, not the full year.
    """
    first, last = rng.get("first"), rng.get("last")
    if year is None or first is None:
        return Period(year=None, start=None, end=None)
    start = max(date(year, 1, 1), first)
    end = min(date(year, 12, 31), last)
    prev_start, prev_end = _shift_year(start, -1), _shift_year(end, -1)
    if prev_end < first:
        prev_start = prev_end = None
    else:
        prev_start = max(prev_start, first)
    return Period(year=year, start=start, end=end, prev_start=prev_start, prev_end=prev_end)


# ------------------------------------------------------------------ queries

@safe(default=list)
def available_years():
    return [r["year"] for r in fetch(
        "SELECT DISTINCT [year] FROM dbo.vw_MonthlyRevenue ORDER BY [year]")]


@safe(default=None)
def kpi_summary(start=None, end=None):
    rows = call_procedure("sp_GetKpiSummary", StartDate=start, EndDate=end)
    return rows[0] if rows else None


@safe(default=list)
def monthly_revenue(year=None):
    return call_procedure("sp_GetMonthlyRevenue", Year=year)


@safe(default=list)
def top_categories(start=None, end=None, top_n=8):
    return call_procedure("sp_GetCategoryPerformance", StartDate=start, EndDate=end, TopN=top_n)


@safe(default=list)
def region_sales(start=None, end=None):
    return call_procedure("sp_GetRegionSales", StartDate=start, EndDate=end)


@safe(default=list)
def top_products(category=None, top_n=10):
    return call_procedure("sp_GetTopProductsByCategory", Category=category, TopN=top_n)


@safe(default=list)
def category_summary():
    return fetch("""
        SELECT category, total_orders, total_units_sold, total_revenue,
               average_price, revenue_share_pct, revenue_rank
        FROM dbo.vw_CategorySalesSummary
        ORDER BY revenue_rank""")


@safe(default=list)
def delivery_trend(year=None):
    sql = """
        SELECT [year], [month], month_start, delivered_orders, avg_delivery_days,
               late_rate_pct, avg_review_score
        FROM dbo.vw_DeliveryPerformance"""
    if year is None:
        return fetch(sql + " ORDER BY month_start")
    return fetch(sql + " WHERE [year] = %s ORDER BY month_start", [int(year)])


@safe(default=list)
def review_vs_delay():
    return fetch("""
        SELECT bucket_order, delay_bucket, orders, avg_review_score, low_score_pct
        FROM dbo.vw_ReviewVsDelay
        ORDER BY bucket_order""")


@safe(default=list)
def recent_etl_runs(limit=5):
    return fetch("""
        SELECT TOP (%s) run_id, started_at, finished_at, status, triggered_by, source,
               rows_read, rows_loaded, rows_rejected, error_message
        FROM dbo.EtlRunLog
        ORDER BY run_id DESC""", [limit])


@safe(default=list)
def warehouse_categories():
    return [r["category"] for r in fetch(
        "SELECT DISTINCT category FROM dbo.DimProduct ORDER BY category")]


# ------------------------------------------------------------------ helpers for views

def pct_change(current, previous):
    if current is None or not previous:
        return None
    return (current - previous) / previous * 100


def split_complete_months(rows):
    """Separate export cut-off months (a handful of orders) from real months."""
    complete = [r for r in rows if (r.get("orders") or 0) >= INCOMPLETE_MONTH_ORDERS]
    partial = [r for r in rows if (r.get("orders") or 0) < INCOMPLETE_MONTH_ORDERS]
    return complete, partial
