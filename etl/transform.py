"""Turn validated Olist tables into star-schema DataFrames.

Facts keep NATURAL keys (product_id, customer_id, city/state). The load step
swaps them for surrogate keys inside SQL Server, so this module needs no DB.
"""
import unicodedata

import numpy as np
import pandas as pd

STATE_REGION = {
    **dict.fromkeys(["AC", "AP", "AM", "PA", "RO", "RR", "TO"], "North"),
    **dict.fromkeys(["AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"], "Northeast"),
    **dict.fromkeys(["DF", "GO", "MT", "MS"], "Center-West"),
    **dict.fromkeys(["ES", "MG", "RJ", "SP"], "Southeast"),
    **dict.fromkeys(["PR", "RS", "SC"], "South"),
}

# Brazil's bounding box, used to throw away bad geolocation points
LAT_RANGE = (-34.0, 5.5)
LNG_RANGE = (-74.0, -34.0)


def normalize_city(series):
    """'São Paulo ' -> 'sao paulo' so customer and geolocation cities match."""
    def clean(value):
        if not isinstance(value, str):
            return "unknown"
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        return " ".join(value.lower().replace("'", " ").split()) or "unknown"
    return series.map(clean)


def normalize_state(series):
    return series.fillna("NA").str.upper().str.strip().str[:2]


def to_date_key(series):
    """Timestamp -> 20171002 (nullable Int64)."""
    return pd.to_numeric(series.dt.strftime("%Y%m%d"), errors="coerce").astype("Int64")


def build_dim_date(*date_series):
    """Continuous calendar from 1 Jan of the first year to 31 Dec of the last.

    Power BI time intelligence (TOTALYTD, SAMEPERIODLASTYEAR) needs every day
    present, so the calendar must not have gaps.
    """
    all_dates = pd.concat([s.dropna() for s in date_series])
    start = pd.Timestamp(year=all_dates.min().year, month=1, day=1)
    end = pd.Timestamp(year=all_dates.max().year, month=12, day=31)
    dates = pd.date_range(start, end, freq="D")
    return pd.DataFrame({
        "date_key": dates.strftime("%Y%m%d").astype(int),
        "date": dates,
        "day": dates.day,
        "month": dates.month,
        "month_name": dates.strftime("%B"),
        "quarter": dates.quarter,
        "year": dates.year,
        "year_month": dates.strftime("%Y-%m"),
        "weekday": dates.strftime("%A"),
        "weekday_num": dates.weekday + 1,
        "is_weekend": (dates.weekday >= 5).astype(int),
    })


def build_dim_location(customers, geolocation=None):
    loc = customers[["city", "state"]].drop_duplicates().copy()
    loc["region"] = loc["state"].map(STATE_REGION).fillna("Unknown")
    loc["country"] = "Brazil"
    checks = []

    if geolocation is not None and not geolocation.empty:
        geo = geolocation.copy()
        geo["city"] = normalize_city(geo["geolocation_city"])
        geo["state"] = normalize_state(geo["geolocation_state"])
        inside = (geo["geolocation_lat"].between(*LAT_RANGE)
                  & geo["geolocation_lng"].between(*LNG_RANGE))
        checks.append({"dataset": "geolocation", "check": "Point outside Brazil",
                       "rows": int((~inside).sum()), "action": "ignored"})
        geo = (geo[inside].groupby(["city", "state"], as_index=False)
               .agg(latitude=("geolocation_lat", "mean"), longitude=("geolocation_lng", "mean")))
        loc = loc.merge(geo, on=["city", "state"], how="left")
    else:
        loc["latitude"] = np.nan
        loc["longitude"] = np.nan

    loc["latitude"] = loc["latitude"].round(6)
    loc["longitude"] = loc["longitude"].round(6)
    return loc[["city", "state", "region", "country", "latitude", "longitude"]], checks


def build_dim_customer(customers):
    dim = customers.rename(columns={"customer_zip_code_prefix": "zip_prefix"})
    dim["zip_prefix"] = dim["zip_prefix"].fillna("").str.zfill(5).str[:5]
    return dim[["customer_id", "customer_unique_id", "zip_prefix", "city", "state"]]


def build_dim_product(products, translation):
    prod = products.merge(translation, on="product_category_name", how="left")
    prod["category_pt"] = prod["product_category_name"]
    prod["category"] = (prod["product_category_name_english"]
                        .fillna(prod["product_category_name"])
                        .fillna("unknown")
                        .str.lower().str.strip())
    prod["weight_g"] = prod["product_weight_g"]
    prod["volume_cm3"] = (prod["product_length_cm"] * prod["product_height_cm"]
                          * prod["product_width_cm"])
    prod["photos_qty"] = prod.get("product_photos_qty")
    return prod[["product_id", "category", "category_pt", "weight_g", "volume_cm3", "photos_qty"]]


def transform(data):
    """data: dict of clean DataFrames keyed like validators.DATASETS.

    Returns (tables, checks) where tables maps table name -> DataFrame.
    """
    checks = []

    customers = data["customers"].copy()
    customers["city"] = normalize_city(customers["customer_city"])
    customers["state"] = normalize_state(customers["customer_state"])

    orders = data["orders"]
    items = data["order_items"]
    products = data["products"]

    # --- referential checks: drop rows that would break foreign keys ---
    orphan_orders = ~orders["customer_id"].isin(customers["customer_id"])
    checks.append({"dataset": "orders", "check": "Order with unknown customer",
                   "rows": int(orphan_orders.sum()), "action": "rejected"})
    orders = orders[~orphan_orders]

    orphan_items = (~items["order_id"].isin(orders["order_id"])
                    | ~items["product_id"].isin(products["product_id"]))
    checks.append({"dataset": "order_items", "check": "Item with unknown order or product",
                   "rows": int(orphan_items.sum()), "action": "rejected"})
    items = items[~orphan_items]

    # --- dimensions ---
    dim_location, geo_checks = build_dim_location(customers, data.get("geolocation"))
    checks += geo_checks
    dim_customer = build_dim_customer(customers)
    dim_product = build_dim_product(products, data["category_translation"])
    dim_date = build_dim_date(orders["order_purchase_timestamp"],
                              orders["order_delivered_customer_date"],
                              orders["order_estimated_delivery_date"])

    # --- FactSales (one row per order item) ---
    order_cust = orders[["order_id", "customer_id", "order_purchase_timestamp"]].merge(
        customers[["customer_id", "city", "state"]], on="customer_id", how="inner")
    fact = items.merge(order_cust, on="order_id", how="inner")

    # Allocate the real order payment to items in proportion to (price + freight)
    pay_total = data["payments"].groupby("order_id")["payment_value"].sum()
    fact["item_gross"] = fact["price"] + fact["freight_value"]
    order_gross = fact.groupby("order_id")["item_gross"].transform("sum")
    order_paid = fact["order_id"].map(pay_total)
    allocated = (order_paid * fact["item_gross"] / order_gross.replace(0, np.nan))
    fact["payment_value"] = allocated.fillna(fact["item_gross"]).round(2)

    fact_sales = pd.DataFrame({
        "order_id": fact["order_id"],
        "order_item_id": fact["order_item_id"].astype(int),
        "product_id": fact["product_id"],
        "customer_id": fact["customer_id"],
        "date_key": to_date_key(fact["order_purchase_timestamp"]),
        "city": fact["city"],
        "state": fact["state"],
        "seller_id": fact["seller_id"],
        "quantity": 1,                       # Olist stores one row per unit
        "price": fact["price"].round(2),
        "freight": fact["freight_value"].round(2),
        "payment_value": fact["payment_value"],
    })

    # --- FactOrders (one row per order) ---
    item_agg = items.groupby("order_id").agg(items_count=("order_item_id", "count"),
                                             items_value=("price", "sum"),
                                             freight_value=("freight_value", "sum"))
    pays = data["payments"]
    main_type = (pays.sort_values("payment_value", ascending=False)
                 .drop_duplicates("order_id").set_index("order_id")["payment_type"])
    pay_agg = pays.groupby("order_id").agg(payment_value=("payment_value", "sum"),
                                           payment_installments=("payment_installments", "max"))
    reviews = data["reviews"].sort_values("review_answer_timestamp")
    latest_review = reviews.drop_duplicates("order_id", keep="last").set_index("order_id")["review_score"]

    fo = orders.merge(customers[["customer_id", "city", "state"]], on="customer_id", how="inner")
    fo = fo.join(item_agg, on="order_id").join(pay_agg, on="order_id")
    fo["payment_type"] = fo["order_id"].map(main_type)
    fo["review_score"] = fo["order_id"].map(latest_review)

    purchase = fo["order_purchase_timestamp"].dt.normalize()
    delivered = fo["order_delivered_customer_date"].dt.normalize()
    estimated = fo["order_estimated_delivery_date"].dt.normalize()
    delivery_days = (delivered - purchase).dt.days
    bad_delivery = delivery_days < 0
    checks.append({"dataset": "orders", "check": "Delivered before purchase",
                   "rows": int(bad_delivery.sum()), "action": "delivery dates set to NULL"})
    delivered = delivered.mask(bad_delivery)
    delivery_days = delivery_days.mask(bad_delivery)
    delay_days = (delivered - estimated).dt.days

    fact_orders = pd.DataFrame({
        "order_id": fo["order_id"],
        "customer_id": fo["customer_id"],
        "city": fo["city"],
        "state": fo["state"],
        "purchase_date_key": to_date_key(fo["order_purchase_timestamp"]),
        "delivered_date_key": to_date_key(delivered),
        "estimated_date_key": to_date_key(estimated),
        "order_status": fo["order_status"].fillna("unknown").str.lower(),
        "items_count": fo["items_count"].fillna(0).astype(int),
        "items_value": fo["items_value"].fillna(0).round(2),
        "freight_value": fo["freight_value"].fillna(0).round(2),
        "payment_value": fo["payment_value"].fillna(0).round(2),
        "payment_type": fo["payment_type"],
        "payment_installments": fo["payment_installments"].astype("Int64"),
        "delivery_days": delivery_days.astype("Int64"),
        "estimated_days": (estimated - purchase).dt.days.astype("Int64"),
        "delay_days": delay_days.astype("Int64"),
        "is_late": (delay_days > 0).where(delay_days.notna()).astype("boolean").astype("Int64"),
        "review_score": fo["review_score"].astype("Int64"),
    })

    tables = {
        "DimDate": dim_date,
        "DimLocation": dim_location,
        "DimCustomer": dim_customer,
        "DimProduct": dim_product,
        "FactSales": fact_sales,
        "FactOrders": fact_orders,
    }
    checks = [c for c in checks if c["rows"]]
    return tables, checks
