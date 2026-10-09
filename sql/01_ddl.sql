-- =============================================================================
-- 01_ddl.sql — Margin-DB Schema: All Tables (CREATE TABLE IF NOT EXISTS only)
-- =============================================================================
-- This file is SAFE to re-run on an existing schema.
-- It uses CREATE TABLE IF NOT EXISTS throughout — no DROP TABLE statements.
-- For a full destructive reset use sql/reset_db.sql (requires operator confirmation).
--
-- Applies to: MySQL 8.0 | Database: margin_db
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. CONFIG — runtime key-value store (DB_ALLOCATED_MB, AT_RISK_FACTOR, etc.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS CONFIG (
    config_key   VARCHAR(64)  NOT NULL,
    config_value VARCHAR(255) NOT NULL,
    PRIMARY KEY (config_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 2. COMPANY_SEGMENT — standalone dimension (Gaming, Productivity & Business
--    Cloud, More Personal Computing)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS COMPANY_SEGMENT (
    segment_id INT          NOT NULL AUTO_INCREMENT,
    name       VARCHAR(50)  NOT NULL,
    PRIMARY KEY (segment_id),
    UNIQUE KEY uq_segment_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 3. DIM_DATE — date dimension covering 2022-07-01 to 2027-06-30
--    (Microsoft fiscal year ends June 30)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DIM_DATE (
    date_id         INT       NOT NULL,
    calendar_date   DATE      NULL,
    fiscal_year     SMALLINT  NULL,
    fiscal_quarter  TINYINT   NULL,
    is_black_friday BOOLEAN   NULL,
    is_holiday      BOOLEAN   NULL,
    PRIMARY KEY (date_id),
    UNIQUE KEY uq_calendar_date (calendar_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 4. DIM_DEVICE — device/platform dimension; oem non-null only for Windows PC
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DIM_DEVICE (
    device_id     INT         NOT NULL AUTO_INCREMENT,
    platform      VARCHAR(20) NULL,
    device_family VARCHAR(50) NULL,
    oem           VARCHAR(50) NULL,
    PRIMARY KEY (device_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 5. DIM_REGION — geographic region dimension (Americas, EMEA, APAC)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DIM_REGION (
    region_id     INT         NOT NULL AUTO_INCREMENT,
    name          VARCHAR(50) NULL,
    parent_region VARCHAR(50) NULL,
    PRIMARY KEY (region_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 6. DIM_CHANNEL — sales channel dimension (Digital, Physical, Subscription)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DIM_CHANNEL (
    channel_id INT         NOT NULL AUTO_INCREMENT,
    name       VARCHAR(50) NULL,
    PRIMARY KEY (channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 7. PRODUCT — product catalog; FK→COMPANY_SEGMENT
--    At least 100 products seeded in 06_seed_reference.sql
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS PRODUCT (
    product_id      INT            NOT NULL AUTO_INCREMENT,
    segment_id      INT            NOT NULL,
    name            VARCHAR(100)   NULL,
    sku             VARCHAR(20)    NULL,
    unit_cost_ratio DECIMAL(5,4)   NULL,
    PRIMARY KEY (product_id),
    CONSTRAINT fk_product_segment
        FOREIGN KEY (segment_id) REFERENCES COMPANY_SEGMENT (segment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 8. USER_SEGMENT_MAP — maps DB usernames to allowed segment IDs
--    analyst_demo→Gaming only; analyst_all→all segments
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS USER_SEGMENT_MAP (
    db_user    VARCHAR(64) NOT NULL,
    segment_id INT         NOT NULL,
    PRIMARY KEY (db_user, segment_id),
    CONSTRAINT fk_usm_segment
        FOREIGN KEY (segment_id) REFERENCES COMPANY_SEGMENT (segment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 9. FACT_SALES — partitioned sales fact table (Daily_Aggregate grain)
--    NO FK constraints (by design, for partition performance)
--    NO secondary indexes at creation (added via index-recommendation workflow)
--    PK is composite (sale_id, sale_date) to satisfy MySQL partitioning rules
--    PARTITION BY RANGE (TO_DAYS(sale_date)): 20 named quarterly partitions
--    p_FY23Q1..p_FY27Q4 plus p_catch
--
--    Partition boundary = first day of NEXT quarter:
--      Apple FY: Microsoft FY starts July 1; Q1=Jul-Sep, Q2=Oct-Dec,
--                Q3=Jan-Mar, Q4=Apr-Jun
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS FACT_SALES (
    sale_id         BIGINT          NOT NULL AUTO_INCREMENT,
    sale_date       DATE            NOT NULL,
    product_id      INT             NULL,
    segment_id      INT             NULL,
    device_id       INT             NULL,
    region_id       INT             NULL,
    channel_id      INT             NULL,
    qty             INT             NULL,
    gross_amount    DECIMAL(15,2)   NULL,
    discount_amount DECIMAL(15,2)   NULL,
    cost_amount     DECIMAL(15,2)   NULL,
    is_synthetic    BOOLEAN         NOT NULL DEFAULT TRUE,
    PRIMARY KEY (sale_id, sale_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
PARTITION BY RANGE (TO_DAYS(sale_date)) (
    -- FY23: Jul 2022 – Jun 2023
    PARTITION p_FY23Q1 VALUES LESS THAN (TO_DAYS('2022-10-01')),  -- Jul–Sep 2022
    PARTITION p_FY23Q2 VALUES LESS THAN (TO_DAYS('2023-01-01')),  -- Oct–Dec 2022
    PARTITION p_FY23Q3 VALUES LESS THAN (TO_DAYS('2023-04-01')),  -- Jan–Mar 2023
    PARTITION p_FY23Q4 VALUES LESS THAN (TO_DAYS('2023-07-01')),  -- Apr–Jun 2023
    -- FY24: Jul 2023 – Jun 2024
    PARTITION p_FY24Q1 VALUES LESS THAN (TO_DAYS('2023-10-01')),  -- Jul–Sep 2023
    PARTITION p_FY24Q2 VALUES LESS THAN (TO_DAYS('2024-01-01')),  -- Oct–Dec 2023
    PARTITION p_FY24Q3 VALUES LESS THAN (TO_DAYS('2024-04-01')),  -- Jan–Mar 2024
    PARTITION p_FY24Q4 VALUES LESS THAN (TO_DAYS('2024-07-01')),  -- Apr–Jun 2024
    -- FY25: Jul 2024 – Jun 2025
    PARTITION p_FY25Q1 VALUES LESS THAN (TO_DAYS('2024-10-01')),  -- Jul–Sep 2024
    PARTITION p_FY25Q2 VALUES LESS THAN (TO_DAYS('2025-01-01')),  -- Oct–Dec 2024
    PARTITION p_FY25Q3 VALUES LESS THAN (TO_DAYS('2025-04-01')),  -- Jan–Mar 2025
    PARTITION p_FY25Q4 VALUES LESS THAN (TO_DAYS('2025-07-01')),  -- Apr–Jun 2025
    -- FY26: Jul 2025 – Jun 2026
    PARTITION p_FY26Q1 VALUES LESS THAN (TO_DAYS('2025-10-01')),  -- Jul–Sep 2025
    PARTITION p_FY26Q2 VALUES LESS THAN (TO_DAYS('2026-01-01')),  -- Oct–Dec 2025
    PARTITION p_FY26Q3 VALUES LESS THAN (TO_DAYS('2026-04-01')),  -- Jan–Mar 2026
    PARTITION p_FY26Q4 VALUES LESS THAN (TO_DAYS('2026-07-01')),  -- Apr–Jun 2026
    -- FY27: Jul 2026 – Jun 2027
    PARTITION p_FY27Q1 VALUES LESS THAN (TO_DAYS('2026-10-01')),  -- Jul–Sep 2026
    PARTITION p_FY27Q2 VALUES LESS THAN (TO_DAYS('2027-01-01')),  -- Oct–Dec 2026
    PARTITION p_FY27Q3 VALUES LESS THAN (TO_DAYS('2027-04-01')),  -- Jan–Mar 2027
    PARTITION p_FY27Q4 VALUES LESS THAN (TO_DAYS('2027-07-01')),  -- Apr–Jun 2027
    -- Catch-all for any dates beyond FY27
    PARTITION p_catch  VALUES LESS THAN MAXVALUE
);

-- ---------------------------------------------------------------------------
-- 10. QUARTERLY_FINANCIALS — SEC EDGAR quarterly financial data; FK→COMPANY_SEGMENT
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS QUARTERLY_FINANCIALS (
    qf_id            INT           NOT NULL AUTO_INCREMENT,
    fiscal_year      SMALLINT      NULL,
    fiscal_quarter   TINYINT       NULL,
    segment_id       INT           NOT NULL,
    revenue          DECIMAL(15,2) NULL,
    cogs             DECIMAL(15,2) NULL,
    operating_income DECIMAL(15,2) NULL,
    filed_date       DATE          NULL,
    PRIMARY KEY (qf_id),
    CONSTRAINT fk_qf_segment
        FOREIGN KEY (segment_id) REFERENCES COMPANY_SEGMENT (segment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 11. ORDERS — order header; FKs to DIM_DEVICE, DIM_REGION, DIM_CHANNEL
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ORDERS (
    order_id   INT         NOT NULL AUTO_INCREMENT,
    order_date DATE        NULL,
    device_id  INT         NOT NULL,
    region_id  INT         NOT NULL,
    channel_id INT         NOT NULL,
    status     VARCHAR(20) NULL,
    PRIMARY KEY (order_id),
    CONSTRAINT fk_orders_device
        FOREIGN KEY (device_id)  REFERENCES DIM_DEVICE  (device_id),
    CONSTRAINT fk_orders_region
        FOREIGN KEY (region_id)  REFERENCES DIM_REGION  (region_id),
    CONSTRAINT fk_orders_channel
        FOREIGN KEY (channel_id) REFERENCES DIM_CHANNEL (channel_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 12. ORDER_ITEM — weak entity; composite PK (order_id, line_no)
--     FKs to ORDERS and PRODUCT
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ORDER_ITEM (
    order_id   INT            NOT NULL,
    line_no    INT            NOT NULL,
    product_id INT            NOT NULL,
    qty        INT            NULL,
    unit_price DECIMAL(10,2) NULL,
    discount   DECIMAL(5,4)  NULL,
    unit_cost  DECIMAL(10,2) NULL,
    PRIMARY KEY (order_id, line_no),
    CONSTRAINT fk_order_item_order
        FOREIGN KEY (order_id)   REFERENCES ORDERS  (order_id),
    CONSTRAINT fk_order_item_product
        FOREIGN KEY (product_id) REFERENCES PRODUCT (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 13. PRICING_RULE — product pricing with optimistic-lock version; FK→PRODUCT
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS PRICING_RULE (
    rule_id        INT            NOT NULL AUTO_INCREMENT,
    product_id     INT            NOT NULL,
    effective_date DATE           NULL,
    price_value    DECIMAL(10,4)  NULL,
    version        INT            NOT NULL DEFAULT 1,
    PRIMARY KEY (rule_id),
    CONSTRAINT fk_pricing_rule_product
        FOREIGN KEY (product_id) REFERENCES PRODUCT (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 14. AUDIT_LOG — append-only audit trail; populated by PRICING_RULE triggers
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS AUDIT_LOG (
    log_id     BIGINT      NOT NULL AUTO_INCREMENT,
    table_name VARCHAR(64) NULL,
    operation  VARCHAR(10) NULL,
    old_value  JSON        NULL,
    new_value  JSON        NULL,
    db_user    VARCHAR(64) NULL,
    at         DATETIME(3) NULL,
    PRIMARY KEY (log_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 15. CONFLICT_LOG — records optimistic-lock conflicts from sp_update_pricing_rule
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS CONFLICT_LOG (
    conflict_id     BIGINT      NOT NULL AUTO_INCREMENT,
    rule_id         INT         NULL,
    attempted_by    VARCHAR(64) NULL,
    stale_version   INT         NULL,
    current_version INT         NULL,
    at              DATETIME(3) NULL,
    PRIMARY KEY (conflict_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 16. BUSINESS_EVENT — game-launch and promotional events
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS BUSINESS_EVENT (
    event_id      INT             NOT NULL AUTO_INCREMENT,
    event_type    VARCHAR(30)     NULL,
    name          VARCHAR(200)    NULL,
    start_date    DATE            NULL,
    end_date      DATE            NULL,
    expected_lift DECIMAL(5,4)   NULL,
    source_url    VARCHAR(500)    NULL,
    PRIMARY KEY (event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 17. STEAM_PLAYER — Steam player-count observations; UNIQUE(app_id, obs_date)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS STEAM_PLAYER (
    sp_id        BIGINT          NOT NULL AUTO_INCREMENT,
    app_id       INT             NULL,
    obs_date     DATE            NULL,
    avg_players  INT UNSIGNED    NULL,
    peak_players INT UNSIGNED    NULL,
    PRIMARY KEY (sp_id),
    UNIQUE KEY uq_steam_player_app_date (app_id, obs_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 18. EVENT_WINDOW_DAY — weak entity; composite PK (event_id, day_offset)
--     day_offset range: -14 to +30; FK→BUSINESS_EVENT
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS EVENT_WINDOW_DAY (
    event_id   INT            NOT NULL,
    day_offset SMALLINT       NOT NULL,
    lift_ratio DECIMAL(8,4)   NULL,
    PRIMARY KEY (event_id, day_offset),
    CONSTRAINT fk_ewd_event
        FOREIGN KEY (event_id) REFERENCES BUSINESS_EVENT (event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 19. GOOGLE_TREND — Google Trends keyword interest observations
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS GOOGLE_TREND (
    gt_id          INT             NOT NULL AUTO_INCREMENT,
    keyword        VARCHAR(200)    NULL,
    obs_date       DATE            NULL,
    interest_score TINYINT UNSIGNED NULL,
    PRIMARY KEY (gt_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 20. QUERY_EXECUTION — query telemetry written by svc_telemetry
--     INDEX on (flagged, started_at DESC) for slow-query queue queries
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS QUERY_EXECUTION (
    qe_id         BIGINT           NOT NULL AUTO_INCREMENT,
    db_user       VARCHAR(64)      NULL,
    digest_text   TEXT             NULL,
    started_at    DATETIME(3)      NULL,
    duration_ms   DECIMAL(12,3)    NULL,
    rows_examined INT UNSIGNED     NULL,
    rows_sent     INT UNSIGNED     NULL,
    flagged       TINYINT(1)       NOT NULL DEFAULT 0,
    PRIMARY KEY (qe_id),
    INDEX idx_qe_flagged_started (flagged, started_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 21. QUERY_PLAN_NODE — weak entity; composite PK (qe_id, node_id)
--     FK→QUERY_EXECUTION; stores EXPLAIN ANALYZE plan tree nodes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS QUERY_PLAN_NODE (
    qe_id          BIGINT          NOT NULL,
    node_id        SMALLINT        NOT NULL,
    parent_node_id SMALLINT        NULL,
    operation      VARCHAR(200)    NULL,
    table_name     VARCHAR(64)     NULL,
    pct_time       DECIMAL(6,2)    NULL,
    est_rows       BIGINT          NULL,
    actual_rows    BIGINT          NULL,
    PRIMARY KEY (qe_id, node_id),
    CONSTRAINT fk_qpn_qe
        FOREIGN KEY (qe_id) REFERENCES QUERY_EXECUTION (qe_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 22. INDEX_RECOMMENDATION — index workflow records; FK→QUERY_EXECUTION
--     Indexes for deduplication check and qe_id lookup
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS INDEX_RECOMMENDATION (
    rec_id      INT             NOT NULL AUTO_INCREMENT,
    qe_id       BIGINT          NOT NULL,
    table_name  VARCHAR(64)     NULL,
    columns_csv VARCHAR(500)    NULL,
    index_name  VARCHAR(128)    NULL,
    status      VARCHAR(20)     NOT NULL DEFAULT 'proposed',
    before_ms   DECIMAL(12,3)   NULL,
    after_ms    DECIMAL(12,3)   NULL,
    decided_by  VARCHAR(64)     NULL,
    decided_at  DATETIME(3)     NULL,
    PRIMARY KEY (rec_id),
    CONSTRAINT fk_ir_qe
        FOREIGN KEY (qe_id) REFERENCES QUERY_EXECUTION (qe_id),
    INDEX idx_ir_qe_id (qe_id),
    INDEX idx_ir_table_cols_status (table_name, columns_csv, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 23. DB_SIZE_SNAPSHOT — daily database size snapshots for capacity forecast
--     UNIQUE on snapped_at prevents duplicate daily entries
--     INDEX on snapped_at DESC for 90-day linear regression window
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS DB_SIZE_SNAPSHOT (
    snap_id        INT            NOT NULL AUTO_INCREMENT,
    snapped_at     DATE           NOT NULL,
    total_data_mb  DECIMAL(12,2)  NULL,
    total_index_mb DECIMAL(12,2)  NULL,
    is_synthetic   BOOLEAN        NOT NULL DEFAULT FALSE,
    PRIMARY KEY (snap_id),
    UNIQUE KEY uq_snapshot_date (snapped_at),
    INDEX idx_snapshot_date_desc (snapped_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 24. FORECAST — forecast run header; FK→BUSINESS_EVENT (nullable for DB forecasts)
--     kind: 'business' or 'database'
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS FORECAST (
    forecast_id INT         NOT NULL AUTO_INCREMENT,
    kind        VARCHAR(20) NULL,
    event_id    INT         NULL,
    as_of_date  DATE        NULL,
    created_at  DATETIME(3) NULL,
    PRIMARY KEY (forecast_id),
    CONSTRAINT fk_forecast_event
        FOREIGN KEY (event_id) REFERENCES BUSINESS_EVENT (event_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- 25. FORECAST_LINE — weak entity; composite PK (forecast_id, line_no)
--     FK→FORECAST; stores individual metric lines per forecast run
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS FORECAST_LINE (
    forecast_id      INT             NOT NULL,
    line_no          INT             NOT NULL,
    metric_name      VARCHAR(100)    NULL,
    metric_value     DECIMAL(20,4)   NULL,
    error_indicator  VARCHAR(200)    NULL,
    PRIMARY KEY (forecast_id, line_no),
    CONSTRAINT fk_fl_forecast
        FOREIGN KEY (forecast_id) REFERENCES FORECAST (forecast_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =============================================================================
-- End of 01_ddl.sql
-- Total tables: 25 (CONFIG + 24 named tables per requirements)
-- Tables: CONFIG, COMPANY_SEGMENT, DIM_DATE, DIM_DEVICE, DIM_REGION,
--         DIM_CHANNEL, PRODUCT, USER_SEGMENT_MAP, FACT_SALES,
--         QUARTERLY_FINANCIALS, ORDERS, ORDER_ITEM, PRICING_RULE,
--         AUDIT_LOG, CONFLICT_LOG, BUSINESS_EVENT, STEAM_PLAYER,
--         EVENT_WINDOW_DAY, GOOGLE_TREND, QUERY_EXECUTION,
--         QUERY_PLAN_NODE, INDEX_RECOMMENDATION, DB_SIZE_SNAPSHOT,
--         FORECAST, FORECAST_LINE
-- =============================================================================
