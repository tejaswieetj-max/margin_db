-- =============================================================================
-- heavy_demo_query.sql — Stage 2 Gate Sequential-Scan Verification Query
--
-- Purpose:
--   Demonstrate full table scan on FACT_SALES (via v_sales) before the
--   candidate index is created, and >= 50% speedup after the index is added.
--
-- Requirements met:
--   - Spans at least four fiscal quarters (FY23Q1 through FY26Q4 = 16 quarters)
--   - Joins FACT_SALES (via v_sales) to dimension data (region_name, channel_name)
--   - Groups by at least two columns (region_name, channel_name)
--   - Must take > 3 seconds against the full dataset with no secondary index
--     (Requirement 4.11)
--
-- Run as analyst_all (has access to all segments):
--   mysql -u analyst_all -p$DEMO_ANALYST_ALL_PASSWORD $DB_NAME < tests/heavy_demo_query.sql
-- =============================================================================

-- Enable query timing output
SET profiling = 1;

-- Heavy_Demo_Query: revenue summary spanning FY23Q1 through FY26Q4
-- by region and channel, forcing a full scan of FACT_SALES via v_sales
SELECT
    region_name,
    channel_name,
    fiscal_year,
    fiscal_quarter,
    COUNT(*)                        AS sale_count,
    SUM(gross_amount)               AS total_revenue,
    SUM(discount_amount)            AS total_discounts,
    AVG(gross_amount)               AS avg_sale_amount,
    SUM(qty)                        AS total_units
FROM v_sales
WHERE sale_date BETWEEN '2022-07-01' AND '2026-06-30'
  AND product_id IS NOT NULL
GROUP BY
    region_name,
    channel_name,
    fiscal_year,
    fiscal_quarter
ORDER BY
    fiscal_year,
    fiscal_quarter,
    total_revenue DESC;

-- Show query profile (timing)
SHOW PROFILES;
