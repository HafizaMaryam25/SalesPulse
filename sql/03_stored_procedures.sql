/* =====================================================================
   SalesPulse  |  03_stored_procedures.sql
   Parameterised queries called by the Django KPI page.
   SET NOCOUNT ON is required so pyodbc receives the result set
   instead of a "rows affected" message.
   ===================================================================== */

USE SalesPulseDB;
GO

/* 1. Headline KPIs for a date range (NULL = no limit) */
CREATE OR ALTER PROCEDURE dbo.sp_GetKpiSummary
    @StartDate DATE = NULL,
    @EndDate   DATE = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.fn_KpiSummary(@StartDate, @EndDate);
END;
GO

/* 2. Monthly revenue for one year (NULL = every month) */
CREATE OR ALTER PROCEDURE dbo.sp_GetMonthlyRevenue
    @Year INT = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SELECT [year], [month], month_start, revenue, freight, orders, avg_order_value,
           running_revenue, ytd_revenue, prev_month_revenue, mom_growth_pct
    FROM dbo.vw_MonthlyRevenue
    WHERE @Year IS NULL OR [year] = @Year
    ORDER BY month_start;
END;
GO

/* 3. Top N products, either inside one category or overall (@Category = NULL) */
CREATE OR ALTER PROCEDURE dbo.sp_GetTopProductsByCategory
    @Category NVARCHAR(100) = NULL,
    @TopN     INT = 10
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP (@TopN)
           product_id, category, units_sold, revenue, avg_price,
           rank_in_category, overall_rank
    FROM dbo.vw_TopProducts
    WHERE @Category IS NULL OR category = @Category
    ORDER BY CASE WHEN @Category IS NULL THEN overall_rank ELSE rank_in_category END, revenue DESC;
END;
GO

/* 4. Category performance for a date range, ranked, with share of revenue */
CREATE OR ALTER PROCEDURE dbo.sp_GetCategoryPerformance
    @StartDate DATE = NULL,
    @EndDate   DATE = NULL,
    @TopN      INT  = 10
AS
BEGIN
    SET NOCOUNT ON;
    WITH cat AS (
        SELECT
            p.category,
            COUNT(DISTINCT f.order_id) AS orders,
            SUM(f.quantity)            AS units,
            SUM(f.price * f.quantity)  AS revenue
        FROM dbo.FactSales f
        JOIN dbo.DimProduct p ON p.product_key = f.product_key
        JOIN dbo.DimDate d    ON d.date_key    = f.date_key
        WHERE (@StartDate IS NULL OR d.[date] >= @StartDate)
          AND (@EndDate   IS NULL OR d.[date] <= @EndDate)
        GROUP BY p.category
    ),
    ranked AS (
        SELECT *,
               RANK() OVER (ORDER BY revenue DESC)                      AS revenue_rank,
               100.0 * revenue / NULLIF(SUM(revenue) OVER (), 0)        AS share_pct
        FROM cat
    )
    SELECT category, orders, units,
           CAST(revenue   AS DECIMAL(14,2)) AS revenue,
           CAST(share_pct AS DECIMAL(6,2))  AS revenue_share_pct,
           revenue_rank
    FROM ranked
    WHERE revenue_rank <= @TopN
    ORDER BY revenue_rank;
END;
GO

/* 5. Revenue and orders by Brazilian region for a date range */
CREATE OR ALTER PROCEDURE dbo.sp_GetRegionSales
    @StartDate DATE = NULL,
    @EndDate   DATE = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        l.region,
        COUNT(DISTINCT f.order_id)                 AS orders,
        CAST(SUM(f.price * f.quantity) AS DECIMAL(14,2)) AS revenue,
        COUNT(DISTINCT l.state)                    AS states
    FROM dbo.FactSales f
    JOIN dbo.DimLocation l ON l.location_key = f.location_key
    JOIN dbo.DimDate d     ON d.date_key     = f.date_key
    WHERE (@StartDate IS NULL OR d.[date] >= @StartDate)
      AND (@EndDate   IS NULL OR d.[date] <= @EndDate)
    GROUP BY l.region
    ORDER BY revenue DESC;
END;
GO

PRINT 'SalesPulse stored procedures created.';

/* Quick tests
EXEC dbo.sp_GetKpiSummary @StartDate = '2018-01-01', @EndDate = '2018-08-31';
EXEC dbo.sp_GetMonthlyRevenue @Year = 2017;
EXEC dbo.sp_GetTopProductsByCategory @Category = 'health_beauty', @TopN = 5;
EXEC dbo.sp_GetCategoryPerformance @TopN = 10;
EXEC dbo.sp_GetRegionSales;
*/
