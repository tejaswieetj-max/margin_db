-- =============================================================================
-- 03_views.sql  —  All 8 views with SQL SECURITY DEFINER
--
-- All views are owned by ADMIN_USER (SQL SECURITY DEFINER).
-- MySQL syntax: CREATE [OR REPLACE] [SQL SECURITY { DEFINER | INVOKER }] VIEW ...
-- Segment filter (v_sales, v_financials only):
--   WHERE segment_id IN (
--     SELECT segment_id FROM USER_SEGMENT_MAP
--     WHERE db_user = SUBSTRING_INDEX(USER(),'@',1)
--   )
-- Per-user filter (v_my_queries only):
--   WHERE QUERY_EXECUTION.db_user = SUBSTRING_INDEX(USER(),'@',1)
-- FACT_SALES has NO alias in v_sales (keeps EXPLAIN ANALYZE output unambiguous).
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. v_sales
--    Joins FACT_SALES (no alias) → PRODUCT, DIM_DATE, DIM_DEVICE,
--    DIM_REGION (as region_name), DIM_CHANNEL (as channel_name).
--    Segment-filtered via USER_SEGMENT_MAP.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_sales;
CREATE SQL SECURITY DEFINER VIEW v_sales AS
SELECT
    FACT_SALES.sale_id,
    FACT_SALES.sale_date,
    FACT_SALES.qty,
    FACT_SALES.gross_amount,
    FACT_SALES.discount_amount,
    FACT_SALES.cost_amount,
    FACT_SALES.is_synthetic,
    PRODUCT.product_id,
    PRODUCT.name                  AS product_name,
    PRODUCT.sku,
    PRODUCT.unit_cost_ratio,
    PRODUCT.product_line,
    DIM_DATE.date_id,
    DIM_DATE.fiscal_year,
    DIM_DATE.fiscal_quarter,
    DIM_DATE.is_black_friday,
    DIM_DATE.is_holiday,
    DIM_DEVICE.platform,
    DIM_DEVICE.device_family,
    DIM_DEVICE.oem,
    DIM_REGION.name               AS region_name,
    DIM_CHANNEL.name              AS channel_name
FROM FACT_SALES
JOIN PRODUCT     ON FACT_SALES.product_id  = PRODUCT.product_id
JOIN DIM_DATE    ON FACT_SALES.sale_date   = DIM_DATE.calendar_date
JOIN DIM_DEVICE  ON FACT_SALES.device_id   = DIM_DEVICE.device_id
JOIN DIM_REGION  ON FACT_SALES.region_id   = DIM_REGION.region_id
JOIN DIM_CHANNEL ON FACT_SALES.channel_id  = DIM_CHANNEL.channel_id
WHERE FACT_SALES.segment_id IN (
    SELECT m.segment_id FROM USER_SEGMENT_MAP m
    WHERE m.db_user = SUBSTRING_INDEX(USER(),'@',1)
      AND (m.product_line IS NULL
           OR m.product_line = PRODUCT.product_line)
);

-- -----------------------------------------------------------------------------
-- 2. v_financials
--    QUARTERLY_FINANCIALS joined to COMPANY_SEGMENT.
--    Segment-filtered via USER_SEGMENT_MAP.
--    NOTE: analyst_demo is mapped to MPC; this view returns MPC financials.
--    Real Gaming revenue is NOT in QUARTERLY_FINANCIALS; see GAMING_REVENUE_SERIES.
--    Users with product_line restrictions see only the parent segment financials.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_financials;
CREATE SQL SECURITY DEFINER VIEW v_financials AS
SELECT
    QUARTERLY_FINANCIALS.qf_id,
    QUARTERLY_FINANCIALS.fiscal_year,
    QUARTERLY_FINANCIALS.fiscal_quarter,
    QUARTERLY_FINANCIALS.revenue,
    QUARTERLY_FINANCIALS.cogs,
    QUARTERLY_FINANCIALS.operating_income,
    QUARTERLY_FINANCIALS.filed_date,
    COMPANY_SEGMENT.segment_id,
    COMPANY_SEGMENT.name          AS segment_name
FROM QUARTERLY_FINANCIALS
JOIN COMPANY_SEGMENT ON QUARTERLY_FINANCIALS.segment_id = COMPANY_SEGMENT.segment_id
WHERE QUARTERLY_FINANCIALS.segment_id IN (
    SELECT segment_id FROM USER_SEGMENT_MAP
    WHERE db_user = SUBSTRING_INDEX(USER(),'@',1)
);

-- -----------------------------------------------------------------------------
-- 3. v_events
--    Full BUSINESS_EVENT rows — no filter.
--    Includes source_url and expected_lift.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_events;
CREATE SQL SECURITY DEFINER VIEW v_events AS
SELECT
    event_id,
    event_type,
    name,
    start_date,
    end_date,
    expected_lift,
    source_url
FROM BUSINESS_EVENT;

-- -----------------------------------------------------------------------------
-- 4. v_forecast_business
--    FORECAST joined to FORECAST_LINE where kind = 'business'.
--    No filter.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_forecast_business;
CREATE SQL SECURITY DEFINER VIEW v_forecast_business AS
SELECT
    FORECAST.forecast_id,
    FORECAST.kind,
    FORECAST.event_id,
    FORECAST.as_of_date,
    FORECAST.created_at,
    FORECAST_LINE.line_no,
    FORECAST_LINE.metric_name,
    FORECAST_LINE.metric_value,
    FORECAST_LINE.error_indicator
FROM FORECAST
JOIN FORECAST_LINE ON FORECAST.forecast_id = FORECAST_LINE.forecast_id
WHERE FORECAST.kind = 'business';

-- -----------------------------------------------------------------------------
-- 5. v_my_queries
--    QUERY_EXECUTION LEFT JOIN INDEX_RECOMMENDATION.
--    Per-user filter: db_user = SUBSTRING_INDEX(USER(),'@',1).
--    (NOT a segment filter.)
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_my_queries;
CREATE SQL SECURITY DEFINER VIEW v_my_queries AS
SELECT
    QUERY_EXECUTION.qe_id,
    QUERY_EXECUTION.db_user,
    QUERY_EXECUTION.digest_text,
    QUERY_EXECUTION.started_at,
    QUERY_EXECUTION.duration_ms,
    QUERY_EXECUTION.rows_examined,
    QUERY_EXECUTION.rows_sent,
    QUERY_EXECUTION.flagged,
    INDEX_RECOMMENDATION.rec_id,
    INDEX_RECOMMENDATION.table_name,
    INDEX_RECOMMENDATION.columns_csv,
    INDEX_RECOMMENDATION.index_name,
    INDEX_RECOMMENDATION.status         AS rec_status,
    INDEX_RECOMMENDATION.before_ms,
    INDEX_RECOMMENDATION.after_ms,
    INDEX_RECOMMENDATION.decided_by,
    INDEX_RECOMMENDATION.decided_at
FROM QUERY_EXECUTION
LEFT JOIN INDEX_RECOMMENDATION ON QUERY_EXECUTION.qe_id = INDEX_RECOMMENDATION.qe_id
WHERE QUERY_EXECUTION.db_user = SUBSTRING_INDEX(USER(),'@',1);

-- -----------------------------------------------------------------------------
-- 6. v_events_min
--    Four columns only from BUSINESS_EVENT: name, start_date, end_date,
--    expected_lift. NO revenue columns. No filter.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_events_min;
CREATE SQL SECURITY DEFINER VIEW v_events_min AS
SELECT
    name,
    start_date,
    end_date,
    expected_lift
FROM BUSINESS_EVENT;

-- -----------------------------------------------------------------------------
-- 7. v_forecast_database
--    FORECAST joined to FORECAST_LINE where kind = 'database'.
--    No filter.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_forecast_database;
CREATE SQL SECURITY DEFINER VIEW v_forecast_database AS
SELECT
    FORECAST.forecast_id,
    FORECAST.kind,
    FORECAST.event_id,
    FORECAST.as_of_date,
    FORECAST.created_at,
    FORECAST_LINE.line_no,
    FORECAST_LINE.metric_name,
    FORECAST_LINE.metric_value,
    FORECAST_LINE.error_indicator
FROM FORECAST
JOIN FORECAST_LINE ON FORECAST.forecast_id = FORECAST_LINE.forecast_id
WHERE FORECAST.kind = 'database';

-- -----------------------------------------------------------------------------
-- 8. v_partitions
--    Partition metadata for FACT_SALES from information_schema.
--    No filter.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS v_partitions;
CREATE SQL SECURITY DEFINER VIEW v_partitions AS
SELECT *
FROM information_schema.PARTITIONS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'FACT_SALES';
