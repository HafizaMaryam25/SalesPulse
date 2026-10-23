/* =====================================================================
   SalesPulse  |  02_views.sql
   Reporting layer used by the Django KPI page, the REST API and Power BI.
   Safe to re-run (CREATE OR ALTER needs SQL Server 2016 SP1 or later).

   Revenue definition used everywhere: SUM(price * quantity)
   (product revenue, freight excluded).  Freight is reported separately.
   ===================================================================== */

USE SalesPulseDB;
GO

/* ---------- KPI function: one row of headline numbers for any date range ----------
   Used by vw_KpiSummary (all time) and sp_GetKpiSummary (date filtered),
   so the logic lives in exactly one place. */
CREATE OR ALTER FUNCTION dbo.fn_KpiSummary (@StartDate DATE, @EndDate DATE)
RETURNS TABLE
AS
RETURN
WITH sales AS (
    SELECT
        SUM(f.price * f.quantity)   AS revenue,
        SUM(f.freight)              AS freight,
        SUM(f.quantity)             AS units,
        COUNT(DISTINCT f.order_id)  AS orders
    FROM dbo.FactSales f
    JOIN dbo.DimDate d ON d.date_key = f.date_key
    WHERE (@StartDate IS NULL OR d.[date] >= @StartDate)
      AND (@EndDate   IS NULL OR d.[date] <= @EndDate)
),
ord AS (
    SELECT o.order_id, c.customer_unique_id, o.is_late, o.review_score, o.delivery_days
    FROM dbo.FactOrders o
    JOIN dbo.DimCustomer c ON c.customer_key = o.customer_key
    JOIN dbo.DimDate d     ON d.date_key     = o.purchase_date_key
    WHERE (@StartDate IS NULL OR d.[date] >= @StartDate)
      AND (@EndDate   IS NULL OR d.[date] <= @EndDate)
),
cust AS (
    SELECT customer_unique_id, COUNT(*) AS order_count
    FROM ord
    GROUP BY customer_unique_id
)
SELECT
    CAST(ISNULL(s.revenue, 0) AS DECIMAL(14,2))                    AS total_revenue,
    CAST(ISNULL(s.freight, 0) AS DECIMAL(14,2))                    AS total_freight,
    ISNULL(s.orders, 0)                                            AS total_orders,
    ISNULL(s.units, 0)                                             AS total_units,
    CAST(s.revenue / NULLIF(s.orders, 0) AS DECIMAL(12,2))         AS avg_order_value,
    (SELECT COUNT(*) FROM cust)                                    AS total_customers,
    (SELECT COUNT(*) FROM cust WHERE order_count > 1)              AS repeat_customers,
    CAST(100.0 * (SELECT COUNT(*) FROM cust WHERE order_count > 1)
         / NULLIF((SELECT COUNT(*) FROM cust), 0) AS DECIMAL(6,2)) AS repeat_rate_pct,
    (SELECT CAST(AVG(CAST(review_score AS DECIMAL(5,2))) AS DECIMAL(4,2)) FROM ord)   AS avg_review_score,
    (SELECT CAST(100.0 * SUM(CASE WHEN is_late = 1 THEN 1 ELSE 0 END)
                 / NULLIF(COUNT(is_late), 0) AS DECIMAL(6,2)) FROM ord)              AS late_delivery_rate_pct,
    (SELECT CAST(AVG(CAST(delivery_days AS DECIMAL(7,2))) AS DECIMAL(6,2)) FROM ord) AS avg_delivery_days
FROM sales s;
GO

CREATE OR ALTER VIEW dbo.vw_KpiSummary AS
SELECT * FROM dbo.fn_KpiSummary(NULL, NULL);
GO

/* ---------- Monthly revenue with running total, YTD and month-over-month (LAG) ---------- */
CREATE OR ALTER VIEW dbo.vw_MonthlyRevenue AS
WITH monthly AS (
    SELECT
        d.[year],
        d.[month],
        DATEFROMPARTS(d.[year], d.[month], 1) AS month_start,
        SUM(f.price * f.quantity)             AS revenue,
        SUM(f.freight)                        AS freight,
        COUNT(DISTINCT f.order_id)            AS orders
    FROM dbo.FactSales f
    JOIN dbo.DimDate d ON d.date_key = f.date_key
    GROUP BY d.[year], d.[month]
)
SELECT
    [year],
    [month],
    month_start,
    CAST(revenue AS DECIMAL(14,2))                                   AS revenue,
    CAST(freight AS DECIMAL(14,2))                                   AS freight,
    orders,
    CAST(revenue / NULLIF(orders, 0) AS DECIMAL(12,2))               AS avg_order_value,
    CAST(SUM(revenue) OVER (ORDER BY month_start
                            ROWS UNBOUNDED PRECEDING) AS DECIMAL(14,2)) AS running_revenue,
    CAST(SUM(revenue) OVER (PARTITION BY [year] ORDER BY month_start
                            ROWS UNBOUNDED PRECEDING) AS DECIMAL(14,2)) AS ytd_revenue,
    CAST(LAG(revenue) OVER (ORDER BY month_start) AS DECIMAL(14,2))  AS prev_month_revenue,
    CAST(100.0 * (revenue - LAG(revenue) OVER (ORDER BY month_start))
         / NULLIF(LAG(revenue) OVER (ORDER BY month_start), 0) AS DECIMAL(9,2)) AS mom_growth_pct
FROM monthly;
GO

/* ---------- Category performance with share of revenue and RANK ---------- */
CREATE OR ALTER VIEW dbo.vw_CategorySalesSummary AS
WITH cat AS (
    SELECT
        p.category,
        COUNT(DISTINCT f.order_id)  AS total_orders,
        SUM(f.quantity)             AS total_units_sold,
        SUM(f.price * f.quantity)   AS total_revenue,
        AVG(f.price)                AS average_price
    FROM dbo.FactSales f
    JOIN dbo.DimProduct p ON p.product_key = f.product_key
    GROUP BY p.category
)
SELECT
    category,
    total_orders,
    total_units_sold,
    CAST(total_revenue AS DECIMAL(14,2))                                  AS total_revenue,
    CAST(average_price AS DECIMAL(12,2))                                  AS average_price,
    CAST(100.0 * total_revenue / SUM(total_revenue) OVER () AS DECIMAL(6,2)) AS revenue_share_pct,
    RANK() OVER (ORDER BY total_revenue DESC)                             AS revenue_rank
FROM cat;
GO

/* ---------- Product ranking overall and inside each category ---------- */
CREATE OR ALTER VIEW dbo.vw_TopProducts AS
WITH prod AS (
    SELECT
        p.product_id,
        p.category,
        SUM(f.quantity)            AS units_sold,
        SUM(f.price * f.quantity)  AS revenue,
        AVG(f.price)               AS avg_price
    FROM dbo.FactSales f
    JOIN dbo.DimProduct p ON p.product_key = f.product_key
    GROUP BY p.product_id, p.category
)
SELECT
    product_id,
    category,
    units_sold,
    CAST(revenue   AS DECIMAL(14,2)) AS revenue,
    CAST(avg_price AS DECIMAL(12,2)) AS avg_price,
    RANK()       OVER (PARTITION BY category ORDER BY revenue DESC) AS rank_in_category,
    DENSE_RANK() OVER (ORDER BY revenue DESC)                       AS overall_rank
FROM prod;
GO

/* ---------- Customers by state: unique customers, orders, repeat rate ---------- */
CREATE OR ALTER VIEW dbo.vw_CustomerMetrics AS
WITH per_customer_state AS (
    SELECT
        c.customer_unique_id,
        l.state,
        l.region,
        COUNT(*)            AS orders,
        SUM(o.items_value)  AS revenue
    FROM dbo.FactOrders o
    JOIN dbo.DimCustomer c ON c.customer_key = o.customer_key
    JOIN dbo.DimLocation l ON l.location_key = o.location_key
    GROUP BY c.customer_unique_id, l.state, l.region
),
flagged AS (
    SELECT *,
           SUM(orders) OVER (PARTITION BY customer_unique_id) AS lifetime_orders
    FROM per_customer_state
)
SELECT
    state,
    region,
    COUNT(*)                                              AS total_customers,
    SUM(orders)                                           AS total_orders,
    CAST(SUM(revenue) AS DECIMAL(14,2))                   AS total_revenue,
    SUM(CASE WHEN lifetime_orders > 1 THEN 1 ELSE 0 END)  AS repeat_customers,
    CAST(100.0 * SUM(CASE WHEN lifetime_orders > 1 THEN 1 ELSE 0 END)
         / NULLIF(COUNT(*), 0) AS DECIMAL(6,2))           AS repeat_rate_pct
FROM flagged
GROUP BY state, region;
GO

/* ---------- One row per real customer with a simple value segment (NTILE) ---------- */
CREATE OR ALTER VIEW dbo.vw_CustomerSegments AS
WITH per_customer AS (
    SELECT
        c.customer_unique_id,
        COUNT(*)              AS orders,
        SUM(o.items_value)    AS revenue,
        MIN(d.[date])         AS first_purchase,
        MAX(d.[date])         AS last_purchase
    FROM dbo.FactOrders o
    JOIN dbo.DimCustomer c ON c.customer_key = o.customer_key
    JOIN dbo.DimDate d     ON d.date_key     = o.purchase_date_key
    GROUP BY c.customer_unique_id
),
scored AS (
    SELECT *,
           NTILE(5) OVER (ORDER BY revenue) AS value_quintile,      -- 5 = top 20% spenders
           DATEDIFF(DAY, last_purchase, MAX(last_purchase) OVER ()) AS recency_days
    FROM per_customer
)
SELECT
    customer_unique_id,
    orders,
    CAST(revenue AS DECIMAL(14,2)) AS revenue,
    first_purchase,
    last_purchase,
    recency_days,
    value_quintile,
    CASE
        WHEN orders >= 2         THEN 'Repeat'
        WHEN value_quintile = 5  THEN 'High-value one-time'
        ELSE 'One-time'
    END AS segment
FROM scored;
GO

/* ---------- Delivery performance by purchase month ---------- */
CREATE OR ALTER VIEW dbo.vw_DeliveryPerformance AS
SELECT
    d.[year],
    d.[month],
    DATEFROMPARTS(d.[year], d.[month], 1)                          AS month_start,
    COUNT(*)                                                       AS delivered_orders,
    CAST(AVG(CAST(o.delivery_days  AS DECIMAL(7,2))) AS DECIMAL(6,2)) AS avg_delivery_days,
    CAST(AVG(CAST(o.estimated_days AS DECIMAL(7,2))) AS DECIMAL(6,2)) AS avg_estimated_days,
    SUM(CASE WHEN o.is_late = 1 THEN 1 ELSE 0 END)                 AS late_orders,
    CAST(100.0 * SUM(CASE WHEN o.is_late = 1 THEN 1 ELSE 0 END)
         / NULLIF(COUNT(*), 0) AS DECIMAL(6,2))                    AS late_rate_pct,
    CAST(AVG(CAST(o.review_score AS DECIMAL(5,2))) AS DECIMAL(4,2))  AS avg_review_score
FROM dbo.FactOrders o
JOIN dbo.DimDate d ON d.date_key = o.purchase_date_key
WHERE o.delivered_date_key IS NOT NULL
GROUP BY d.[year], d.[month];
GO

/* ---------- Review score vs delivery delay (for the Delivery & Reviews page) ---------- */
CREATE OR ALTER VIEW dbo.vw_ReviewVsDelay AS
WITH bucketed AS (
    SELECT
        o.review_score,
        CASE
            WHEN o.delay_days <= -8 THEN 1
            WHEN o.delay_days <= -1 THEN 2
            WHEN o.delay_days  =  0 THEN 3
            WHEN o.delay_days <=  3 THEN 4
            WHEN o.delay_days <=  7 THEN 5
            ELSE 6
        END AS bucket_order
    FROM dbo.FactOrders o
    WHERE o.delay_days IS NOT NULL AND o.review_score IS NOT NULL
)
SELECT
    bucket_order,
    CASE bucket_order
        WHEN 1 THEN '8+ days early'
        WHEN 2 THEN '1-7 days early'
        WHEN 3 THEN 'On the estimated day'
        WHEN 4 THEN '1-3 days late'
        WHEN 5 THEN '4-7 days late'
        ELSE '8+ days late'
    END                                                          AS delay_bucket,
    COUNT(*)                                                     AS orders,
    CAST(AVG(CAST(review_score AS DECIMAL(5,2))) AS DECIMAL(4,2)) AS avg_review_score,
    CAST(100.0 * SUM(CASE WHEN review_score <= 2 THEN 1 ELSE 0 END)
         / COUNT(*) AS DECIMAL(6,2))                             AS low_score_pct
FROM bucketed
GROUP BY bucket_order;
GO

PRINT 'SalesPulse views created.';
