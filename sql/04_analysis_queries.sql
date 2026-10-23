/* =====================================================================
   SalesPulse  |  04_analysis_queries.sql
   Window-function analysis queries. Run them one at a time in SSMS.
   These are good to walk through in an interview.
   ===================================================================== */

USE SalesPulseDB;
GO

/* 1. Running total and 3-month moving average of revenue */
SELECT
    month_start,
    revenue,
    running_revenue,
    CAST(AVG(revenue) OVER (ORDER BY month_start
                            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS DECIMAL(14,2)) AS moving_avg_3m
FROM dbo.vw_MonthlyRevenue
ORDER BY month_start;

/* 2. Month-over-month and year-over-year change with LAG */
SELECT
    month_start,
    revenue,
    LAG(revenue, 1)  OVER (ORDER BY month_start) AS prev_month,
    LAG(revenue, 12) OVER (ORDER BY month_start) AS same_month_last_year,
    CAST(100.0 * (revenue - LAG(revenue, 12) OVER (ORDER BY month_start))
         / NULLIF(LAG(revenue, 12) OVER (ORDER BY month_start), 0) AS DECIMAL(9,2)) AS yoy_growth_pct
FROM dbo.vw_MonthlyRevenue
ORDER BY month_start;

/* 3. Top 3 products in every category (RANK inside PARTITION BY) */
SELECT category, product_id, revenue, rank_in_category
FROM dbo.vw_TopProducts
WHERE rank_in_category <= 3
ORDER BY category, rank_in_category;

/* 4. Best month of each year (ROW_NUMBER) */
WITH ranked AS (
    SELECT [year], [month], revenue,
           ROW_NUMBER() OVER (PARTITION BY [year] ORDER BY revenue DESC) AS rn
    FROM dbo.vw_MonthlyRevenue
)
SELECT [year], [month], revenue
FROM ranked
WHERE rn = 1;

/* 5. Category share of each state's revenue */
WITH state_cat AS (
    SELECT l.state, p.category, SUM(f.price * f.quantity) AS revenue
    FROM dbo.FactSales f
    JOIN dbo.DimLocation l ON l.location_key = f.location_key
    JOIN dbo.DimProduct  p ON p.product_key  = f.product_key
    GROUP BY l.state, p.category
)
SELECT state, category, revenue,
       CAST(100.0 * revenue / SUM(revenue) OVER (PARTITION BY state) AS DECIMAL(6,2)) AS share_of_state_pct,
       RANK() OVER (PARTITION BY state ORDER BY revenue DESC) AS rank_in_state
FROM state_cat
ORDER BY state, rank_in_state;

/* 6. Days between a customer's orders (LEAD) */
WITH cust_orders AS (
    SELECT c.customer_unique_id, d.[date] AS order_date
    FROM dbo.FactOrders o
    JOIN dbo.DimCustomer c ON c.customer_key = o.customer_key
    JOIN dbo.DimDate d     ON d.date_key     = o.purchase_date_key
)
SELECT customer_unique_id, order_date,
       LEAD(order_date) OVER (PARTITION BY customer_unique_id ORDER BY order_date) AS next_order_date,
       DATEDIFF(DAY, order_date,
                LEAD(order_date) OVER (PARTITION BY customer_unique_id ORDER BY order_date)) AS days_to_next_order
FROM cust_orders
ORDER BY customer_unique_id, order_date;

/* 7. Median and 90th percentile delivery time per state (PERCENTILE_CONT) */
SELECT DISTINCT
    l.state,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY o.delivery_days) OVER (PARTITION BY l.state) AS median_delivery_days,
    PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY o.delivery_days) OVER (PARTITION BY l.state) AS p90_delivery_days
FROM dbo.FactOrders o
JOIN dbo.DimLocation l ON l.location_key = o.location_key
WHERE o.delivery_days IS NOT NULL
ORDER BY median_delivery_days DESC;

/* 8. Monthly cohort retention: of customers who first bought in month X,
      how many bought again in a later month? */
WITH first_buy AS (
    SELECT c.customer_unique_id,
           MIN(DATEFROMPARTS(d.[year], d.[month], 1)) AS cohort_month
    FROM dbo.FactOrders o
    JOIN dbo.DimCustomer c ON c.customer_key = o.customer_key
    JOIN dbo.DimDate d     ON d.date_key     = o.purchase_date_key
    GROUP BY c.customer_unique_id
),
activity AS (
    SELECT DISTINCT c.customer_unique_id,
           DATEFROMPARTS(d.[year], d.[month], 1) AS active_month
    FROM dbo.FactOrders o
    JOIN dbo.DimCustomer c ON c.customer_key = o.customer_key
    JOIN dbo.DimDate d     ON d.date_key     = o.purchase_date_key
)
SELECT f.cohort_month,
       DATEDIFF(MONTH, f.cohort_month, a.active_month) AS months_since_first,
       COUNT(*) AS active_customers
FROM first_buy f
JOIN activity a ON a.customer_unique_id = f.customer_unique_id
GROUP BY f.cohort_month, DATEDIFF(MONTH, f.cohort_month, a.active_month)
ORDER BY f.cohort_month, months_since_first;

/* 9. Last 10 ETL runs */
SELECT TOP 10 run_id, started_at, finished_at, status, triggered_by, source,
       rows_read, rows_loaded, rows_rejected, error_message
FROM dbo.EtlRunLog
ORDER BY run_id DESC;
