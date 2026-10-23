"""SalesPulse ETL: Olist CSV files -> SQL Server star schema.

Run from the project root (the folder that contains etl/ and webapp/):

    python -m etl.etl_pipeline              # full load into SQL Server
    python -m etl.etl_pipeline --dry-run    # validate + transform only, no database

Idempotent: every table is loaded with a MERGE on its natural key, so running
the pipeline twice inserts nothing the second time.
"""
import argparse
import json
import sys
import time
from pathlib import Path

if __package__ in (None, ""):          # allow `python etl/etl_pipeline.py` too
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from etl import config
from etl.transform import transform
from etl.validators import DATASETS, validate_file

# Table name -> (natural key columns, SELECT that maps natural keys to surrogate keys)
LOAD_PLAN = {
    "DimDate": (["date_key"], """
        SELECT date_key, CAST([date] AS DATE) AS [date], [day], [month], month_name, quarter,
               [year], year_month, weekday, weekday_num, CAST(is_weekend AS BIT) AS is_weekend
        FROM dbo.stg_DimDate"""),
    "DimLocation": (["city", "state"], """
        SELECT city, state, region, country,
               CAST(latitude AS DECIMAL(9,6)) AS latitude,
               CAST(longitude AS DECIMAL(9,6)) AS longitude
        FROM dbo.stg_DimLocation"""),
    "DimCustomer": (["customer_id"], "SELECT * FROM dbo.stg_DimCustomer"),
    "DimProduct": (["product_id"], """
        SELECT product_id, category, category_pt,
               CAST(weight_g AS DECIMAL(10,2)) AS weight_g,
               CAST(volume_cm3 AS DECIMAL(12,2)) AS volume_cm3,
               CAST(photos_qty AS SMALLINT) AS photos_qty
        FROM dbo.stg_DimProduct"""),
    "FactSales": (["order_id", "order_item_id"], """
        SELECT s.order_id, CAST(s.order_item_id AS SMALLINT) AS order_item_id,
               p.product_key, c.customer_key, s.date_key, l.location_key, s.seller_id,
               s.quantity,
               CAST(s.price AS DECIMAL(12,2))         AS price,
               CAST(s.freight AS DECIMAL(12,2))       AS freight,
               CAST(s.payment_value AS DECIMAL(12,2)) AS payment_value
        FROM dbo.stg_FactSales s
        JOIN dbo.DimProduct  p ON p.product_id  = s.product_id
        JOIN dbo.DimCustomer c ON c.customer_id = s.customer_id
        JOIN dbo.DimLocation l ON l.city = s.city AND l.state = s.state
        JOIN dbo.DimDate     d ON d.date_key    = s.date_key"""),
    "FactOrders": (["order_id"], """
        SELECT s.order_id, c.customer_key, l.location_key,
               s.purchase_date_key, s.delivered_date_key, s.estimated_date_key,
               s.order_status, s.items_count,
               CAST(s.items_value AS DECIMAL(12,2))   AS items_value,
               CAST(s.freight_value AS DECIMAL(12,2)) AS freight_value,
               CAST(s.payment_value AS DECIMAL(12,2)) AS payment_value,
               s.payment_type, s.payment_installments,
               s.delivery_days, s.estimated_days, s.delay_days,
               CAST(s.is_late AS BIT)                 AS is_late,
               s.review_score
        FROM dbo.stg_FactOrders s
        JOIN dbo.DimCustomer c ON c.customer_id = s.customer_id
        JOIN dbo.DimLocation l ON l.city = s.city AND l.state = s.state"""),
}

TARGET_COLUMNS = {
    "DimDate": ["date_key", "date", "day", "month", "month_name", "quarter", "year",
                "year_month", "weekday", "weekday_num", "is_weekend"],
    "DimLocation": ["city", "state", "region", "country", "latitude", "longitude"],
    "DimCustomer": ["customer_id", "customer_unique_id", "zip_prefix", "city", "state"],
    "DimProduct": ["product_id", "category", "category_pt", "weight_g", "volume_cm3", "photos_qty"],
    "FactSales": ["order_id", "order_item_id", "product_key", "customer_key", "date_key",
                  "location_key", "seller_id", "quantity", "price", "freight", "payment_value"],
    "FactOrders": ["order_id", "customer_key", "location_key", "purchase_date_key",
                   "delivered_date_key", "estimated_date_key", "order_status", "items_count",
                   "items_value", "freight_value", "payment_value", "payment_type",
                   "payment_installments", "delivery_days", "estimated_days", "delay_days",
                   "is_late", "review_score"],
}


def log(msg):
    print(f"[ETL {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- extract

def extract(data_dir):
    """Read and validate every dataset. Raises ValueError on a fatal problem."""
    data, reports = {}, {}
    for name, spec in DATASETS.items():
        path = Path(data_dir) / spec.filename
        if not path.exists():
            if spec.optional:
                log(f"skip {spec.filename} (optional, not found)")
                continue
            raise ValueError(f"Missing file: {path}")
        result = validate_file(name, path)
        if not result.ok:
            raise ValueError(f"{spec.filename}: {'; '.join(result.errors)}")
        data[name] = result.df
        reports[name] = result
        log(f"read {spec.filename}: {result.rows_read:,} rows, {result.rows_rejected:,} rejected")
    return data, reports


# ---------------------------------------------------------------- load

def merge_sql(table, keys, source_sql):
    cols = TARGET_COLUMNS[table]
    non_keys = [c for c in cols if c not in keys]
    on = " AND ".join(f"t.[{k}] = s.[{k}]" for k in keys)
    s_cols = ", ".join(f"s.[{c}]" for c in non_keys)
    t_cols = ", ".join(f"t.[{c}]" for c in non_keys)
    set_list = ", ".join(f"t.[{c}] = s.[{c}]" for c in non_keys)
    insert_cols = ", ".join(f"[{c}]" for c in cols)
    insert_vals = ", ".join(f"s.[{c}]" for c in cols)
    # "EXISTS (SELECT s.. EXCEPT SELECT t..)" is a NULL-safe "has anything changed?"
    return f"""
SET NOCOUNT ON;
DECLARE @out TABLE (act NVARCHAR(10));
DECLARE @src INT = (SELECT COUNT(*) FROM ({source_sql}) AS x);
MERGE dbo.{table} WITH (HOLDLOCK) AS t
USING ({source_sql}) AS s
   ON {on}
WHEN MATCHED AND EXISTS (SELECT {s_cols} EXCEPT SELECT {t_cols}) THEN
   UPDATE SET {set_list}
WHEN NOT MATCHED BY TARGET THEN
   INSERT ({insert_cols}) VALUES ({insert_vals})
OUTPUT $action INTO @out;
SELECT @src AS source_rows,
       ISNULL(SUM(CASE WHEN act = 'INSERT' THEN 1 ELSE 0 END), 0) AS inserted,
       ISNULL(SUM(CASE WHEN act = 'UPDATE' THEN 1 ELSE 0 END), 0) AS updated
FROM @out;
"""


def run_batch(engine, sql, params=None):
    """Execute a T-SQL batch and return the first row of its last result set."""
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        while cur.description is None and cur.nextset():
            pass
        row = cur.fetchone() if cur.description else None
        raw.commit()
        return row
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def stage(engine, table, df):
    from sqlalchemy.types import NVARCHAR
    from etl.validators import is_text
    # bounded NVARCHAR instead of NVARCHAR(MAX): faster with fast_executemany
    dtype = {c: NVARCHAR(200) for c in df.columns if is_text(df[c])}
    df.to_sql(f"stg_{table}", engine, schema="dbo", if_exists="replace",
              index=False, chunksize=5000, dtype=dtype)


def load(engine, tables):
    stats = {}
    for table, (keys, source_sql) in LOAD_PLAN.items():
        df = tables[table]
        stage(engine, table, df)
        source_rows, inserted, updated = run_batch(engine, merge_sql(table, keys, source_sql))
        stats[table] = {
            "staged": len(df),
            "matched_dimensions": int(source_rows),
            "dropped_no_dimension": len(df) - int(source_rows),
            "inserted": int(inserted),
            "updated": int(updated),
            "unchanged": int(source_rows) - int(inserted) - int(updated),
        }
        run_batch(engine, f"DROP TABLE IF EXISTS dbo.stg_{table}; SELECT 1;")
        log(f"{table}: +{inserted:,} inserted, {updated:,} updated, "
            f"{stats[table]['unchanged']:,} unchanged")
    return stats


# ---------------------------------------------------------------- run log

def start_run_log(engine, triggered_by, source):
    row = run_batch(engine, """
        SET NOCOUNT ON;
        INSERT INTO dbo.EtlRunLog (status, triggered_by, source)
        OUTPUT INSERTED.run_id
        VALUES ('RUNNING', ?, ?);""", [triggered_by, source])
    return int(row[0])


def finish_run_log(engine, run_id, status, rows_read=None, rows_loaded=None,
                   rows_rejected=None, error=None, details=None):
    run_batch(engine, """
        SET NOCOUNT ON;
        UPDATE dbo.EtlRunLog
           SET finished_at = SYSDATETIME(), status = ?, rows_read = ?, rows_loaded = ?,
               rows_rejected = ?, error_message = ?, details = ?
         WHERE run_id = ?;
        SELECT @@ROWCOUNT;""",
        [status, rows_read, rows_loaded, rows_rejected, error,
         json.dumps(details, default=str) if details else None, run_id])


# ---------------------------------------------------------------- orchestration

def run_etl(data_dir=None, triggered_by="cli", source="full load", dry_run=False):
    """Run the whole pipeline. Returns a summary dict (also stored in EtlRunLog)."""
    started = time.time()
    data_dir = Path(data_dir or config.DATA_DIR)
    engine = None if dry_run else config.get_engine()
    run_id = None if dry_run else start_run_log(engine, triggered_by, source)
    log(f"run {run_id or '(dry run)'} started by {triggered_by}, data in {data_dir}")

    try:
        data, reports = extract(data_dir)
        tables, transform_checks = transform(data)
        checks = [c for r in reports.values() for c in r.checks] + transform_checks
        for c in checks:
            log(f"check {c['dataset']}: {c['check']} -> {c['rows']:,} rows {c['action']}")

        rows_read = sum(r.rows_read for r in reports.values())
        rows_rejected = sum(r.rows_rejected for r in reports.values()) + sum(
            c["rows"] for c in transform_checks if c["action"] == "rejected")

        summary = {
            "run_id": run_id,
            "status": "SUCCESS",
            "rows_read": rows_read,
            "rows_rejected": rows_rejected,
            "datasets": {n: {"rows_read": r.rows_read, "rows_rejected": r.rows_rejected}
                         for n, r in reports.items()},
            "checks": checks,
            "tables": {t: {"rows": len(df)} for t, df in tables.items()},
        }

        if dry_run:
            summary["rows_loaded"] = 0
        else:
            stats = load(engine, tables)
            summary["tables"] = stats
            summary["rows_loaded"] = sum(s["inserted"] + s["updated"] for s in stats.values())
            summary["rows_rejected"] += sum(s["dropped_no_dimension"] for s in stats.values())

        summary["duration_sec"] = round(time.time() - started, 1)
        if run_id:
            finish_run_log(engine, run_id, "SUCCESS", summary["rows_read"],
                           summary["rows_loaded"], summary["rows_rejected"], details=summary)
        log(f"done in {summary['duration_sec']}s: {summary['rows_read']:,} read, "
            f"{summary['rows_loaded']:,} loaded, {summary['rows_rejected']:,} rejected")
        return summary

    except Exception as exc:
        log(f"FAILED: {exc}")
        if run_id:
            finish_run_log(engine, run_id, "FAILED", error=str(exc)[:4000])
        raise


def main():
    parser = argparse.ArgumentParser(description="SalesPulse ETL")
    parser.add_argument("--data-dir", default=None, help="folder with the Olist CSV files")
    parser.add_argument("--dry-run", action="store_true", help="validate and transform only")
    parser.add_argument("--triggered-by", default="cli")
    args = parser.parse_args()
    try:
        run_etl(args.data_dir, triggered_by=args.triggered_by, dry_run=args.dry_run)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
