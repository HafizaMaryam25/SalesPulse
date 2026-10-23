# Power BI dashboard

The finished report is [`SalesPulse.pbix`](SalesPulse.pbix) (open it in the free Power BI Desktop).
Screenshots are in [`../docs/screenshots/`](../docs/screenshots). The steps below document how it was built.

## 1. Connect

1. **Get data > SQL Server**. Server: `localhost` (or `localhost\SQLEXPRESS`), Database: `SalesPulseDB`, mode **Import**.
2. Tick these and click **Load**:
   `FactSales`, `FactOrders`, `DimDate`, `DimCustomer`, `DimProduct`, `DimLocation`,
   `vw_CustomerSegments`, `vw_ReviewVsDelay`.

## 2. Model (Model view)

| From (many) | To (one) | Active |
|---|---|---|
| FactSales[date_key] | DimDate[date_key] | yes |
| FactSales[product_key] | DimProduct[product_key] | yes |
| FactSales[customer_key] | DimCustomer[customer_key] | yes |
| FactSales[location_key] | DimLocation[location_key] | yes |
| FactOrders[purchase_date_key] | DimDate[date_key] | yes |
| FactOrders[delivered_date_key] | DimDate[date_key] | **no** (used with USERELATIONSHIP) |
| FactOrders[estimated_date_key] | DimDate[date_key] | **no** |
| FactOrders[customer_key] | DimCustomer[customer_key] | yes |
| FactOrders[location_key] | DimLocation[location_key] | yes |
| DimCustomer[customer_unique_id] | vw_CustomerSegments[customer_unique_id] | yes (segments = one side) |

Then:

- Select `DimDate` > **Table tools > Mark as date table** > column `date`. Without this, YTD and YoY measures return wrong numbers.
- Sort `DimDate[month_name]` by `DimDate[month]` (Column tools > Sort by column).
- Sort `vw_ReviewVsDelay[delay_bucket]` by `bucket_order`.
- Set `DimLocation[state]` Data category = **State or Province**, `latitude` / `longitude` = Latitude / Longitude.
- Hide key columns (`*_key`) from report view.

## 3. Measures

Copy every measure from [`measures.dax`](measures.dax) into a `_Measures` table.
Format: Revenue measures as currency R$ (0 decimals), rates as percentage (1 decimal).

## 4. Pages

All pages share slicers for **DimDate[year]**, **DimLocation[region]** and **DimProduct[category]**.
Use View > Sync slicers so a choice on one page carries to the others.

### Executive Overview
- Cards: Revenue, Orders, Avg Order Value, Revenue YoY %, Estimated Gross Profit (labelled *Estimated*).
- Line chart: Revenue by `DimDate[year_month]`, with Revenue PY as a second line.
- Column chart: Revenue MoM % by month (conditional colour: red below 0).
- Revenue YTD card.

### Products & Categories
- Bar chart: Top 10 categories by Revenue (Filters pane > Top N = 10 by Revenue).
- Bar chart: Bottom 10 categories (Top N, Bottom 10).
- Scatter: `DimProduct[category]` as details, X = Avg Item Price, Y = Units Sold, size = Revenue (price vs volume).
- Table: category, Revenue, Category Share of Revenue, Category Revenue Rank.

### Customers
- Cards: Customers, Repeat Customer Rate, Revenue per Customer.
- Filled map: `DimLocation[state]`, colour saturation = Revenue.
- Donut: `vw_CustomerSegments[segment]` by count of customer_unique_id.
- Bar chart: Top 10 states by Customers.

### Delivery & Reviews
- Cards: Late Delivery Rate, Avg Delivery Days, Avg Review Score.
- Line chart: Late Delivery Rate by month.
- Column chart: `vw_ReviewVsDelay[delay_bucket]` with `avg_review_score` (the strongest story: late orders average 2.3 stars, on-time orders 4.3).
- Matrix: region x year with Late Delivery Rate (conditional formatting).
