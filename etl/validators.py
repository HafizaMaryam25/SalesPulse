"""Data-quality rules for every Olist file.

Shared by the ETL pipeline and the Django upload page, so a file is
judged by the same rules no matter how it arrives.
"""
from dataclasses import dataclass, field

import pandas as pd
from pandas.api.types import is_object_dtype, is_string_dtype


def is_text(series):
    """True for text columns in both pandas 2 (object) and pandas 3 (str)."""
    return is_object_dtype(series) or is_string_dtype(series)


@dataclass(frozen=True)
class DatasetSpec:
    label: str
    filename: str
    required: tuple
    keys: tuple = ()
    dates: tuple = ()
    required_dates: tuple = ()
    numerics: tuple = ()
    non_negative: tuple = ()
    optional: bool = False


DATASETS = {
    "customers": DatasetSpec(
        label="Customers",
        filename="olist_customers_dataset.csv",
        required=("customer_id", "customer_unique_id", "customer_zip_code_prefix",
                  "customer_city", "customer_state"),
        keys=("customer_id",),
    ),
    "orders": DatasetSpec(
        label="Orders",
        filename="olist_orders_dataset.csv",
        required=("order_id", "customer_id", "order_status", "order_purchase_timestamp",
                  "order_approved_at", "order_delivered_carrier_date",
                  "order_delivered_customer_date", "order_estimated_delivery_date"),
        keys=("order_id",),
        dates=("order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
               "order_delivered_customer_date", "order_estimated_delivery_date"),
        required_dates=("order_purchase_timestamp",),
    ),
    "order_items": DatasetSpec(
        label="Order items",
        filename="olist_order_items_dataset.csv",
        required=("order_id", "order_item_id", "product_id", "seller_id",
                  "shipping_limit_date", "price", "freight_value"),
        keys=("order_id", "order_item_id"),
        dates=("shipping_limit_date",),
        numerics=("order_item_id", "price", "freight_value"),
        non_negative=("price", "freight_value"),
    ),
    "products": DatasetSpec(
        label="Products",
        filename="olist_products_dataset.csv",
        required=("product_id", "product_category_name", "product_weight_g",
                  "product_length_cm", "product_height_cm", "product_width_cm"),
        keys=("product_id",),
        numerics=("product_weight_g", "product_length_cm", "product_height_cm",
                  "product_width_cm", "product_photos_qty"),
        non_negative=("product_weight_g",),
    ),
    "payments": DatasetSpec(
        label="Payments",
        filename="olist_order_payments_dataset.csv",
        required=("order_id", "payment_sequential", "payment_type",
                  "payment_installments", "payment_value"),
        keys=("order_id", "payment_sequential"),
        numerics=("payment_sequential", "payment_installments", "payment_value"),
        non_negative=("payment_value",),
    ),
    "reviews": DatasetSpec(
        label="Reviews",
        filename="olist_order_reviews_dataset.csv",
        required=("review_id", "order_id", "review_score", "review_creation_date",
                  "review_answer_timestamp"),
        keys=("review_id", "order_id"),
        dates=("review_creation_date", "review_answer_timestamp"),
        numerics=("review_score",),
    ),
    "category_translation": DatasetSpec(
        label="Category translation",
        filename="product_category_name_translation.csv",
        required=("product_category_name", "product_category_name_english"),
        keys=("product_category_name",),
    ),
    "geolocation": DatasetSpec(
        label="Geolocation (optional)",
        filename="olist_geolocation_dataset.csv",
        required=("geolocation_zip_code_prefix", "geolocation_lat", "geolocation_lng",
                  "geolocation_city", "geolocation_state"),
        numerics=("geolocation_lat", "geolocation_lng"),
        optional=True,
    ),
}


@dataclass
class ValidationResult:
    dataset: str
    rows_read: int = 0
    rows_rejected: int = 0
    errors: list = field(default_factory=list)      # fatal: the file cannot be used
    checks: list = field(default_factory=list)      # data-quality findings
    df: pd.DataFrame = None

    @property
    def ok(self):
        return not self.errors

    def add_check(self, check, count, action):
        if count:
            self.checks.append({"dataset": self.dataset, "check": check,
                                "rows": int(count), "action": action})


def read_csv(source):
    """Read an Olist CSV as text first, so IDs and zip codes keep leading zeros."""
    return pd.read_csv(source, dtype=str, encoding="utf-8-sig", keep_default_na=True)


def validate_dataframe(name, df):
    """Validate and clean one dataset. Returns a ValidationResult with a clean df."""
    spec = DATASETS[name]
    result = ValidationResult(dataset=name, rows_read=len(df))

    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in spec.required if c not in df.columns]
    if missing:
        result.errors.append(f"Missing required columns: {', '.join(missing)}")
        return result
    if df.empty:
        result.errors.append("The file has a header but no rows.")
        return result

    df = df.copy()
    for col in df.columns:
        if is_text(df[col]):
            df[col] = df[col].str.strip()

    # 1. Rows without a key cannot be loaded
    if spec.keys:
        null_key = df[list(spec.keys)].isna().any(axis=1)
        result.add_check("Missing key value", null_key.sum(), "rejected")
        df = df[~null_key]

    # 2. Exact duplicate keys: keep the first row
    if spec.keys:
        dup = df.duplicated(subset=list(spec.keys), keep="first")
        result.add_check("Duplicate key", dup.sum(), "dropped (kept first)")
        df = df[~dup]

    # 3. Dates: unparseable values become NULL
    for col in spec.dates:
        parsed = pd.to_datetime(df[col], errors="coerce", format="ISO8601")
        bad = parsed.isna() & df[col].notna()
        result.add_check(f"Unreadable date in {col}", bad.sum(), "set to NULL")
        df[col] = parsed
    for col in spec.required_dates:
        missing_date = df[col].isna()
        result.add_check(f"Missing {col}", missing_date.sum(), "rejected")
        df = df[~missing_date]

    # 4. Numbers: unparseable values become NULL, negative money is rejected
    for col in spec.numerics:
        if col not in df.columns:
            continue
        parsed = pd.to_numeric(df[col], errors="coerce")
        bad = parsed.isna() & df[col].notna()
        result.add_check(f"Non-numeric value in {col}", bad.sum(), "set to NULL")
        df[col] = parsed
    for col in spec.non_negative:
        negative = df[col] < 0
        result.add_check(f"Negative {col}", negative.sum(), "rejected")
        df = df[~negative]

    # 5. Dataset-specific rules
    if name == "reviews":
        out_of_range = ~df["review_score"].between(1, 5)
        result.add_check("review_score outside 1-5", out_of_range.sum(), "rejected")
        df = df[~out_of_range]
    if name in ("order_items", "payments"):
        null_money = df[[c for c in spec.non_negative]].isna().any(axis=1)
        result.add_check("Missing amount", null_money.sum(), "rejected")
        df = df[~null_money]

    result.df = df.reset_index(drop=True)
    result.rows_rejected = result.rows_read - len(result.df)
    return result


def validate_file(name, source):
    """Read + validate. `source` can be a path or a file-like object (Django upload)."""
    try:
        df = read_csv(source)
    except UnicodeDecodeError:
        return ValidationResult(dataset=name, errors=["File is not UTF-8 encoded. Save it as CSV UTF-8 and try again."])
    except pd.errors.EmptyDataError:
        return ValidationResult(dataset=name, errors=["The file is empty."])
    except pd.errors.ParserError as exc:
        return ValidationResult(dataset=name, errors=[f"The file is not a valid CSV: {exc}"])
    return validate_dataframe(name, df)
