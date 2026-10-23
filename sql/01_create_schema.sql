/* =====================================================================
   SalesPulse  |  01_create_schema.sql
   Star schema for the Olist e-commerce dataset (SQL Server 2016+).

   Run order:  01_create_schema.sql  ->  python -m etl.etl_pipeline
               ->  02_views.sql  ->  03_stored_procedures.sql

   WARNING: this script DROPS and re-creates the warehouse tables.
   Django's own tables (auth_*, django_*, core_*) are NOT touched.
   ===================================================================== */

IF DB_ID('SalesPulseDB') IS NULL
    CREATE DATABASE SalesPulseDB;
GO

USE SalesPulseDB;
GO

/* ---------- 1. Drop old objects (facts first because of foreign keys) ---------- */
DROP TABLE IF EXISTS dbo.FactSales;
DROP TABLE IF EXISTS dbo.FactOrders;
DROP TABLE IF EXISTS dbo.DimCustomer;
DROP TABLE IF EXISTS dbo.DimProduct;
DROP TABLE IF EXISTS dbo.DimLocation;
DROP TABLE IF EXISTS dbo.DimDate;
DROP TABLE IF EXISTS dbo.EtlRunLog;
GO

/* ---------- 2. Dimensions ---------- */

CREATE TABLE dbo.DimDate (
    date_key        INT          NOT NULL CONSTRAINT PK_DimDate PRIMARY KEY,   -- 20171002
    [date]          DATE         NOT NULL CONSTRAINT UQ_DimDate_date UNIQUE,
    [day]           TINYINT      NOT NULL,
    [month]         TINYINT      NOT NULL,
    month_name      VARCHAR(10)  NOT NULL,
    quarter         TINYINT      NOT NULL,
    [year]          SMALLINT     NOT NULL,
    year_month      CHAR(7)      NOT NULL,      -- '2017-10'
    weekday         VARCHAR(10)  NOT NULL,
    weekday_num     TINYINT      NOT NULL,      -- 1 = Monday
    is_weekend      BIT          NOT NULL
);
CREATE INDEX IX_DimDate_year_month ON dbo.DimDate([year], [month]);
GO

CREATE TABLE dbo.DimLocation (
    location_key    INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_DimLocation PRIMARY KEY,
    city            NVARCHAR(100) NOT NULL,
    state           CHAR(2)       NOT NULL,
    region          VARCHAR(20)   NOT NULL,     -- North, Northeast, Center-West, Southeast, South
    country         VARCHAR(50)   NOT NULL CONSTRAINT DF_DimLocation_country DEFAULT ('Brazil'),
    latitude        DECIMAL(9,6)  NULL,
    longitude       DECIMAL(9,6)  NULL,
    CONSTRAINT UQ_DimLocation_city_state UNIQUE (city, state)
);
CREATE INDEX IX_DimLocation_state ON dbo.DimLocation(state);
GO

CREATE TABLE dbo.DimCustomer (
    customer_key        INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_DimCustomer PRIMARY KEY,
    customer_id         VARCHAR(32)   NOT NULL CONSTRAINT UQ_DimCustomer_customer_id UNIQUE, -- one per order in Olist
    customer_unique_id  VARCHAR(32)   NOT NULL,                                             -- the real person
    zip_prefix          VARCHAR(5)    NULL,
    city                NVARCHAR(100) NOT NULL,
    state               CHAR(2)       NOT NULL
);
CREATE INDEX IX_DimCustomer_unique_id ON dbo.DimCustomer(customer_unique_id);
GO

CREATE TABLE dbo.DimProduct (
    product_key     INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_DimProduct PRIMARY KEY,
    product_id      VARCHAR(32)   NOT NULL CONSTRAINT UQ_DimProduct_product_id UNIQUE,
    category        NVARCHAR(100) NOT NULL,
    category_pt     NVARCHAR(100) NULL,
    weight_g        DECIMAL(10,2) NULL,
    volume_cm3      DECIMAL(12,2) NULL,
    photos_qty      SMALLINT      NULL
);
CREATE INDEX IX_DimProduct_category ON dbo.DimProduct(category);
GO

/* ---------- 3. Facts ---------- */

-- Grain: one row per order ITEM
CREATE TABLE dbo.FactSales (
    sales_key       BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_FactSales PRIMARY KEY,
    order_id        VARCHAR(32)   NOT NULL,
    order_item_id   SMALLINT      NOT NULL,
    product_key     INT           NOT NULL CONSTRAINT FK_FactSales_Product  REFERENCES dbo.DimProduct(product_key),
    customer_key    INT           NOT NULL CONSTRAINT FK_FactSales_Customer REFERENCES dbo.DimCustomer(customer_key),
    date_key        INT           NOT NULL CONSTRAINT FK_FactSales_Date     REFERENCES dbo.DimDate(date_key),
    location_key    INT           NOT NULL CONSTRAINT FK_FactSales_Location REFERENCES dbo.DimLocation(location_key),
    seller_id       VARCHAR(32)   NOT NULL,
    quantity        INT           NOT NULL CONSTRAINT DF_FactSales_qty DEFAULT (1),
    price           DECIMAL(12,2) NOT NULL,
    freight         DECIMAL(12,2) NOT NULL,
    payment_value   DECIMAL(12,2) NOT NULL,     -- order payment allocated to this item
    CONSTRAINT UQ_FactSales_order_item UNIQUE (order_id, order_item_id),   -- makes re-runs idempotent
    CONSTRAINT CK_FactSales_price CHECK (price >= 0 AND freight >= 0)
);
CREATE INDEX IX_FactSales_date     ON dbo.FactSales(date_key)     INCLUDE (price, freight, quantity);
CREATE INDEX IX_FactSales_product  ON dbo.FactSales(product_key)  INCLUDE (price, quantity);
CREATE INDEX IX_FactSales_customer ON dbo.FactSales(customer_key);
CREATE INDEX IX_FactSales_location ON dbo.FactSales(location_key);
GO

-- Grain: one row per ORDER (delivery, payment and review facts live here)
CREATE TABLE dbo.FactOrders (
    order_id              VARCHAR(32)   NOT NULL CONSTRAINT PK_FactOrders PRIMARY KEY,
    customer_key          INT           NOT NULL CONSTRAINT FK_FactOrders_Customer REFERENCES dbo.DimCustomer(customer_key),
    location_key          INT           NOT NULL CONSTRAINT FK_FactOrders_Location REFERENCES dbo.DimLocation(location_key),
    purchase_date_key     INT           NOT NULL CONSTRAINT FK_FactOrders_PurchaseDate  REFERENCES dbo.DimDate(date_key),
    delivered_date_key    INT           NULL     CONSTRAINT FK_FactOrders_DeliveredDate REFERENCES dbo.DimDate(date_key),
    estimated_date_key    INT           NULL     CONSTRAINT FK_FactOrders_EstimatedDate REFERENCES dbo.DimDate(date_key),
    order_status          VARCHAR(20)   NOT NULL,
    items_count           SMALLINT      NOT NULL,
    items_value           DECIMAL(12,2) NOT NULL,
    freight_value         DECIMAL(12,2) NOT NULL,
    payment_value         DECIMAL(12,2) NOT NULL,
    payment_type          VARCHAR(20)   NULL,
    payment_installments  SMALLINT      NULL,
    delivery_days         SMALLINT      NULL,    -- purchase -> delivered
    estimated_days        SMALLINT      NULL,    -- purchase -> estimated
    delay_days            SMALLINT      NULL,    -- delivered - estimated (positive = late)
    is_late               BIT           NULL,
    review_score          TINYINT       NULL
);
CREATE INDEX IX_FactOrders_purchase_date ON dbo.FactOrders(purchase_date_key) INCLUDE (payment_value, is_late, review_score);
CREATE INDEX IX_FactOrders_customer      ON dbo.FactOrders(customer_key);
GO

/* ---------- 4. ETL run log ---------- */

CREATE TABLE dbo.EtlRunLog (
    run_id          INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_EtlRunLog PRIMARY KEY,
    started_at      DATETIME2(0)  NOT NULL CONSTRAINT DF_EtlRunLog_started DEFAULT (SYSDATETIME()),
    finished_at     DATETIME2(0)  NULL,
    status          VARCHAR(20)   NOT NULL,      -- RUNNING, SUCCESS, FAILED
    triggered_by    NVARCHAR(150) NOT NULL,
    source          NVARCHAR(200) NULL,
    rows_read       INT           NULL,
    rows_loaded     INT           NULL,          -- inserted + updated
    rows_rejected   INT           NULL,
    error_message   NVARCHAR(MAX) NULL,
    details         NVARCHAR(MAX) NULL           -- JSON: per-table counts + data-quality checks
);
GO

PRINT 'SalesPulse schema created.';
