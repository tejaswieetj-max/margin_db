-- =============================================================================
-- 08_stage2_schema.sql  —  Stage 2 schema additions and corrections
-- =============================================================================
-- STATUS: FULLY IDEMPOTENT — safe to re-run in any starting state.
-- Each section uses information_schema guards and may be applied repeatedly
-- without error regardless of which steps have already been executed.
--
-- This file is a migration; it does NOT modify 01_ddl.sql in place.
-- Apply via:
--   mysql -h $DB_HOST -P $DB_PORT -u $ADMIN_USER $DB_NAME \
--         < sql/migrations/08_stage2_schema.sql
-- On error: halt, log, do NOT run subsequent migrations (consistent with
-- the halt-on-error policy in run_migrations.sh).
--
-- Changes in this file:
--   1.  STEAM_PLAYER.avg_players: INT UNSIGNED → DECIMAL(12,2)  [guarded]
--   2.  EVENT_WINDOW_DAY → EVENT_WINDOW_MONTH with new columns  [fully guarded;
--         handles three starting states: DAY exists, MONTH exists, neither]
--   3.  Add steam_app_id (nullable) to BUSINESS_EVENT           [guarded]
--   4.  New table GAMING_REVENUE_SERIES                         [CREATE IF NOT EXISTS]
--   5.  Seed CONFIG keys for lift/forecast parameters           [ON DUPLICATE KEY]
--   6.  Grants: SELECT on GAMING_REVENUE_SERIES to business_analyst
--   7.  USER_SEGMENT_MAP: add product_line column               [guarded]
--   8.  PRODUCT: add product_line column                        [guarded]
--   9.  FACT_SALES.cost_amount: make nullable                   [guarded]
--  10.  QUARTERLY_FINANCIALS: add basis, source_url, uq_qf_period [guarded]
--
-- All sections are guarded with information_schema checks and can be
-- re-run safely on a database in any intermediate state.
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. STEAM_PLAYER.avg_players: INT UNSIGNED → DECIMAL(12,2)
--
-- Guard: only MODIFY if the column is not already DECIMAL(12,2).
-- Checks DATA_TYPE = 'int' (covers INT UNSIGNED); if it is already
-- 'decimal' the step is skipped.
-- ---------------------------------------------------------------------------
SET @sp_type = (
    SELECT DATA_TYPE FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'STEAM_PLAYER'
      AND COLUMN_NAME  = 'avg_players'
);
SET @sql_sp = IF(@sp_type = 'int' OR @sp_type = 'int unsigned',
    'ALTER TABLE STEAM_PLAYER
     MODIFY COLUMN avg_players DECIMAL(12,2) NULL
         COMMENT ''Monthly avg concurrent players from Steam; decimal precision preserved from source CSV''',
    'SELECT ''STEAM_PLAYER.avg_players already decimal or column absent'' AS note'
);
PREPARE _s_sp FROM @sql_sp; EXECUTE _s_sp; DEALLOCATE PREPARE _s_sp;


-- ---------------------------------------------------------------------------
-- 2. EVENT_WINDOW_DAY → EVENT_WINDOW_MONTH
--
-- Three possible starting states; each is handled idempotently:
--
--   State A: EVENT_WINDOW_DAY exists, EVENT_WINDOW_MONTH does not.
--     → Add missing columns to DAY (guarded per column), rename to MONTH,
--       then rename/re-add the FK.
--
--   State B: EVENT_WINDOW_MONTH exists (rename already done).
--     → Check each required column on MONTH and add/fix only what is missing.
--       Also guard the FK rename.
--
--   State C: neither table exists (clean schema).
--     → CREATE TABLE EVENT_WINDOW_MONTH directly.
--
-- Column target state on EVENT_WINDOW_MONTH:
--   event_id      INT             NOT NULL  (PK part; already exists)
--   avg_players   DECIMAL(12,2)   NULL
--   lift_ratio    DECIMAL(10,4)   NULL
--   month_offset  SMALLINT        NOT NULL  (renamed from day_offset)
--   PK: (event_id, month_offset)
--   FK: fk_ewm_event → BUSINESS_EVENT(event_id)
-- ---------------------------------------------------------------------------

-- Detect starting state
SET @ewd_exists = (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'EVENT_WINDOW_DAY'
);
SET @ewm_exists = (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'EVENT_WINDOW_MONTH'
);

-- ── State A: EVENT_WINDOW_DAY exists ────────────────────────────────────────
-- 2A-1. Add avg_players to EVENT_WINDOW_DAY if missing
SET @has_ewm_ap = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'EVENT_WINDOW_DAY'
      AND COLUMN_NAME  = 'avg_players'
);
SET @sql_ewd_ap = IF(@ewd_exists = 1 AND @has_ewm_ap = 0,
    'ALTER TABLE EVENT_WINDOW_DAY
     ADD COLUMN avg_players DECIMAL(12,2) NULL
         COMMENT ''Avg Steam concurrent players for this month offset''
         AFTER event_id',
    'SELECT ''EVENT_WINDOW_DAY.avg_players already exists or table absent'' AS note'
);
PREPARE _s1 FROM @sql_ewd_ap; EXECUTE _s1; DEALLOCATE PREPARE _s1;

-- 2A-2. Add lift_ratio_new to EVENT_WINDOW_DAY if missing
--       (We use a temp name to avoid collision with the old lift_ratio column)
SET @has_ewd_lrn = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'EVENT_WINDOW_DAY'
      AND COLUMN_NAME  = 'lift_ratio_new'
);
SET @sql_ewd_lrn = IF(@ewd_exists = 1 AND @has_ewd_lrn = 0,
    'ALTER TABLE EVENT_WINDOW_DAY
     ADD COLUMN lift_ratio_new DECIMAL(10,4) NULL
         COMMENT ''lift_ratio = avg_players / baseline_avg_players; NULL if insufficient baseline''
         AFTER avg_players',
    'SELECT ''EVENT_WINDOW_DAY.lift_ratio_new already exists or table absent'' AS note'
);
PREPARE _s2 FROM @sql_ewd_lrn; EXECUTE _s2; DEALLOCATE PREPARE _s2;

-- 2A-3. Drop old lift_ratio and rename lift_ratio_new → lift_ratio
--       Only if old lift_ratio still exists on EVENT_WINDOW_DAY
SET @has_ewd_lr_old = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'EVENT_WINDOW_DAY'
      AND COLUMN_NAME  = 'lift_ratio'
);
SET @has_ewd_lrn2 = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'EVENT_WINDOW_DAY'
      AND COLUMN_NAME  = 'lift_ratio_new'
);
SET @sql_ewd_lr = IF(@ewd_exists = 1 AND @has_ewd_lr_old = 1 AND @has_ewd_lrn2 = 1,
    'ALTER TABLE EVENT_WINDOW_DAY
     DROP COLUMN lift_ratio,
     CHANGE COLUMN lift_ratio_new lift_ratio DECIMAL(10,4) NULL
         COMMENT ''Lift ratio = avg_players / baseline; NULL if baseline insufficient''',
    'SELECT ''EVENT_WINDOW_DAY lift_ratio swap already done or table absent'' AS note'
);
PREPARE _s3 FROM @sql_ewd_lr; EXECUTE _s3; DEALLOCATE PREPARE _s3;

-- 2A-4. Rename day_offset → month_offset on EVENT_WINDOW_DAY if still day_offset
SET @has_ewd_do = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'EVENT_WINDOW_DAY'
      AND COLUMN_NAME  = 'day_offset'
);
SET @sql_ewd_do = IF(@ewd_exists = 1 AND @has_ewd_do = 1,
    'ALTER TABLE EVENT_WINDOW_DAY
     CHANGE COLUMN day_offset month_offset SMALLINT NOT NULL
         COMMENT ''Month offset: -EVENT_PRE_MONTHS to +EVENT_POST_MONTHS''',
    'SELECT ''EVENT_WINDOW_DAY.day_offset already renamed or table absent'' AS note'
);
PREPARE _s4 FROM @sql_ewd_do; EXECUTE _s4; DEALLOCATE PREPARE _s4;

-- 2A-5. Rename EVENT_WINDOW_DAY → EVENT_WINDOW_MONTH
--       Only if DAY still exists and MONTH does not yet exist
SET @ewd_exists2 = (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'EVENT_WINDOW_DAY'
);
SET @ewm_exists2 = (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'EVENT_WINDOW_MONTH'
);
SET @sql_rename = IF(@ewd_exists2 = 1 AND @ewm_exists2 = 0,
    'RENAME TABLE EVENT_WINDOW_DAY TO EVENT_WINDOW_MONTH',
    'SELECT ''EVENT_WINDOW_DAY already renamed or MONTH already exists'' AS note'
);
PREPARE _s5 FROM @sql_rename; EXECUTE _s5; DEALLOCATE PREPARE _s5;

-- ── State B: EVENT_WINDOW_MONTH exists — ensure each column is correct ───────
-- (These guards run regardless of whether we just renamed or it was already there)

-- 2B-1. Ensure avg_players exists on EVENT_WINDOW_MONTH
SET @has_ewm_ap2 = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'EVENT_WINDOW_MONTH'
      AND COLUMN_NAME  = 'avg_players'
);
SET @sql_ewm_ap = IF(@has_ewm_ap2 = 0,
    'ALTER TABLE EVENT_WINDOW_MONTH
     ADD COLUMN avg_players DECIMAL(12,2) NULL
         COMMENT ''Avg Steam concurrent players for this month offset''
         AFTER event_id',
    'SELECT ''EVENT_WINDOW_MONTH.avg_players already exists'' AS note'
);
PREPARE _s6 FROM @sql_ewm_ap; EXECUTE _s6; DEALLOCATE PREPARE _s6;

-- 2B-2. Ensure lift_ratio exists on EVENT_WINDOW_MONTH as DECIMAL(10,4).
--       If lift_ratio_new accidentally persists (partial prior run), rename it first.
SET @has_ewm_lrn = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'EVENT_WINDOW_MONTH'
      AND COLUMN_NAME  = 'lift_ratio_new'
);
SET @sql_ewm_lrn_fix = IF(@has_ewm_lrn = 1,
    'ALTER TABLE EVENT_WINDOW_MONTH
     CHANGE COLUMN lift_ratio_new lift_ratio DECIMAL(10,4) NULL
         COMMENT ''Lift ratio = avg_players / baseline; NULL if baseline insufficient''',
    'SELECT ''EVENT_WINDOW_MONTH.lift_ratio_new not present; no fix needed'' AS note'
);
PREPARE _s7 FROM @sql_ewm_lrn_fix; EXECUTE _s7; DEALLOCATE PREPARE _s7;

SET @has_ewm_lr = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'EVENT_WINDOW_MONTH'
      AND COLUMN_NAME  = 'lift_ratio'
);
SET @sql_ewm_lr = IF(@has_ewm_lr = 0,
    'ALTER TABLE EVENT_WINDOW_MONTH
     ADD COLUMN lift_ratio DECIMAL(10,4) NULL
         COMMENT ''Lift ratio = avg_players / baseline; NULL if baseline insufficient''
         AFTER avg_players',
    'SELECT ''EVENT_WINDOW_MONTH.lift_ratio already exists'' AS note'
);
PREPARE _s8 FROM @sql_ewm_lr; EXECUTE _s8; DEALLOCATE PREPARE _s8;

-- 2B-3. Rename day_offset → month_offset on EVENT_WINDOW_MONTH if still day_offset
SET @has_ewm_do = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'EVENT_WINDOW_MONTH'
      AND COLUMN_NAME  = 'day_offset'
);
SET @sql_ewm_do = IF(@has_ewm_do = 1,
    'ALTER TABLE EVENT_WINDOW_MONTH
     CHANGE COLUMN day_offset month_offset SMALLINT NOT NULL
         COMMENT ''Month offset: -EVENT_PRE_MONTHS to +EVENT_POST_MONTHS''',
    'SELECT ''EVENT_WINDOW_MONTH.day_offset already renamed'' AS note'
);
PREPARE _s9 FROM @sql_ewm_do; EXECUTE _s9; DEALLOCATE PREPARE _s9;

-- 2B-4. Ensure month_offset column exists even if day_offset never existed
--       (handles clean-create path or future re-runs)
SET @has_ewm_mo = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'EVENT_WINDOW_MONTH'
      AND COLUMN_NAME  = 'month_offset'
);
SET @sql_ewm_mo = IF(@has_ewm_mo = 0,
    'ALTER TABLE EVENT_WINDOW_MONTH
     ADD COLUMN month_offset SMALLINT NOT NULL DEFAULT 0
         COMMENT ''Month offset: -EVENT_PRE_MONTHS to +EVENT_POST_MONTHS''',
    'SELECT ''EVENT_WINDOW_MONTH.month_offset already exists'' AS note'
);
PREPARE _s10 FROM @sql_ewm_mo; EXECUTE _s10; DEALLOCATE PREPARE _s10;

-- ── State C: neither table exists — create EVENT_WINDOW_MONTH directly ───────
-- (Runs only if EVENT_WINDOW_MONTH still does not exist after the above steps)
SET @ewm_final = (
    SELECT COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'EVENT_WINDOW_MONTH'
);
SET @sql_ewm_create = IF(@ewm_final = 0,
    'CREATE TABLE EVENT_WINDOW_MONTH (
        event_id     INT            NOT NULL,
        avg_players  DECIMAL(12,2)  NULL
            COMMENT ''Avg Steam concurrent players for this month offset'',
        lift_ratio   DECIMAL(10,4)  NULL
            COMMENT ''Lift ratio = avg_players / baseline; NULL if baseline insufficient'',
        month_offset SMALLINT       NOT NULL
            COMMENT ''Month offset: -EVENT_PRE_MONTHS to +EVENT_POST_MONTHS'',
        PRIMARY KEY (event_id, month_offset),
        CONSTRAINT fk_ewm_event
            FOREIGN KEY (event_id) REFERENCES BUSINESS_EVENT (event_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci',
    'SELECT ''EVENT_WINDOW_MONTH already exists; skipping CREATE'' AS note'
);
PREPARE _s11 FROM @sql_ewm_create; EXECUTE _s11; DEALLOCATE PREPARE _s11;

-- ---------------------------------------------------------------------------
-- 2b. FK rename: fk_ewd_event → fk_ewm_event
--
-- Guard A: only drop fk_ewd_event if it exists on EVENT_WINDOW_MONTH.
-- Guard B: only add fk_ewm_event if it does not already exist.
-- ---------------------------------------------------------------------------
SET @has_fk_old = (
    SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA    = DATABASE()
      AND TABLE_NAME      = 'EVENT_WINDOW_MONTH'
      AND CONSTRAINT_NAME = 'fk_ewd_event'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);
SET @sql_fk_drop = IF(@has_fk_old = 1,
    'ALTER TABLE EVENT_WINDOW_MONTH DROP FOREIGN KEY fk_ewd_event',
    'SELECT ''fk_ewd_event not present on EVENT_WINDOW_MONTH; nothing to drop'' AS note'
);
PREPARE _s12 FROM @sql_fk_drop; EXECUTE _s12; DEALLOCATE PREPARE _s12;

SET @has_fk_new = (
    SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
    WHERE TABLE_SCHEMA    = DATABASE()
      AND TABLE_NAME      = 'EVENT_WINDOW_MONTH'
      AND CONSTRAINT_NAME = 'fk_ewm_event'
      AND CONSTRAINT_TYPE = 'FOREIGN KEY'
);
SET @sql_fk_add = IF(@has_fk_new = 0,
    'ALTER TABLE EVENT_WINDOW_MONTH
     ADD CONSTRAINT fk_ewm_event
         FOREIGN KEY (event_id) REFERENCES BUSINESS_EVENT (event_id)',
    'SELECT ''fk_ewm_event already exists on EVENT_WINDOW_MONTH'' AS note'
);
PREPARE _s13 FROM @sql_fk_add; EXECUTE _s13; DEALLOCATE PREPARE _s13;


-- ---------------------------------------------------------------------------
-- 3. Add steam_app_id (nullable INT) to BUSINESS_EVENT
-- Guard: only add if not already present.
-- ---------------------------------------------------------------------------
SET @has_sai = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'BUSINESS_EVENT'
      AND COLUMN_NAME  = 'steam_app_id'
);
SET @sql_sai = IF(@has_sai = 0,
    'ALTER TABLE BUSINESS_EVENT
     ADD COLUMN steam_app_id INT NULL
         COMMENT ''Steam app_id for game-launch events; NULL for non-Steam events''
         AFTER expected_lift',
    'SELECT ''BUSINESS_EVENT.steam_app_id already exists'' AS note'
);
PREPARE _s14 FROM @sql_sai; EXECUTE _s14; DEALLOCATE PREPARE _s14;


-- ---------------------------------------------------------------------------
-- 4. New table: GAMING_REVENUE_SERIES
-- CREATE TABLE IF NOT EXISTS is already idempotent.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS GAMING_REVENUE_SERIES (
    grs_id           INT            NOT NULL AUTO_INCREMENT,
    fiscal_year      SMALLINT       NOT NULL,
    fiscal_quarter   TINYINT        NOT NULL,
    revenue_m        DECIMAL(12,2)  NOT NULL
        COMMENT 'Gaming revenue in millions USD. Source: segments.csv',
    operating_income DECIMAL(12,2)  NULL
        COMMENT 'Always NULL. Gaming OI not separately disclosed; never estimated',
    months_label     VARCHAR(40)    NULL
        COMMENT 'Reference label from segments.csv Months column, e.g. Jul 2022 to Sep 2022',
    source_url       VARCHAR(500)   NOT NULL
        COMMENT 'URL of the Microsoft earnings release cited in segments.csv',
    loaded_at        DATETIME(3)    NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (grs_id),
    UNIQUE KEY uq_grs_period (fiscal_year, fiscal_quarter)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Gaming product-line revenue series. Source: segments.csv. Not a COMPANY_SEGMENT row. Gaming sits inside MPC and is kept separate to avoid double-counting. All values in millions USD.';


-- ---------------------------------------------------------------------------
-- 5. Seed CONFIG keys for lift/forecast parameters
-- ON DUPLICATE KEY UPDATE config_value = config_value is a no-op; idempotent.
-- ---------------------------------------------------------------------------
INSERT INTO CONFIG (config_key, config_value) VALUES
    ('EVENT_PRE_MONTHS',     '3'),
    ('EVENT_POST_MONTHS',    '3'),
    ('BASELINE_MONTHS',      '3'),
    ('MIN_BASELINE_MONTHS',  '3'),
    ('FORECAST_LIFT_MONTHS', '3')
ON DUPLICATE KEY UPDATE config_value = config_value;


-- ---------------------------------------------------------------------------
-- 6. Grant: SELECT on GAMING_REVENUE_SERIES to business_analyst
-- GRANT is idempotent in MySQL — re-running does not error.
-- ---------------------------------------------------------------------------
GRANT SELECT ON margin_db.GAMING_REVENUE_SERIES TO 'business_analyst';


-- ---------------------------------------------------------------------------
-- 7. USER_SEGMENT_MAP: add nullable product_line column (guarded)
-- ---------------------------------------------------------------------------
SET @col_usm = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'USER_SEGMENT_MAP'
      AND COLUMN_NAME  = 'product_line'
);
SET @sql_usm = IF(@col_usm = 0,
    'ALTER TABLE USER_SEGMENT_MAP ADD COLUMN product_line VARCHAR(50) NULL COMMENT ''NULL = full-segment access; non-NULL restricts to that product line''',
    'SELECT ''USER_SEGMENT_MAP.product_line already exists'' AS note'
);
PREPARE _s15 FROM @sql_usm; EXECUTE _s15; DEALLOCATE PREPARE _s15;


-- ---------------------------------------------------------------------------
-- 8. PRODUCT: add nullable product_line column (guarded)
-- ---------------------------------------------------------------------------
SET @col_prod = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'PRODUCT'
      AND COLUMN_NAME  = 'product_line'
);
SET @sql_prod = IF(@col_prod = 0,
    'ALTER TABLE PRODUCT ADD COLUMN product_line VARCHAR(50) NULL COMMENT ''Gaming, or NULL for non-tagged products'' AFTER unit_cost_ratio',
    'SELECT ''PRODUCT.product_line already exists'' AS note'
);
PREPARE _s16 FROM @sql_prod; EXECUTE _s16; DEALLOCATE PREPARE _s16;


-- ---------------------------------------------------------------------------
-- 9. FACT_SALES.cost_amount: make nullable (guarded)
-- ---------------------------------------------------------------------------
SET @cs_nullable = (
    SELECT IS_NULLABLE FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'FACT_SALES'
      AND COLUMN_NAME  = 'cost_amount'
);
SET @sql_cs = IF(@cs_nullable = 'NO',
    'ALTER TABLE FACT_SALES MODIFY COLUMN cost_amount DECIMAL(15,2) NULL COMMENT ''NULL for Gaming rows (no cost data disclosed)''',
    'SELECT ''FACT_SALES.cost_amount already nullable'' AS note'
);
PREPARE _s17 FROM @sql_cs; EXECUTE _s17; DEALLOCATE PREPARE _s17;


-- ---------------------------------------------------------------------------
-- 10. QUARTERLY_FINANCIALS: add basis, source_url columns and UNIQUE KEY
--
-- basis values:
--   'as_reported_pre_recast' — filed before the August 2024 segment recast
--   'recast_2024-08'         — restated on the August 2024 recast structure
--   'as_reported'            — post-recast filings going forward
--
-- uq_qf_period: required for ON DUPLICATE KEY UPDATE in load_financials.py.
-- cogs and filed_date are already NULL in 01_ddl.sql — no MODIFY needed.
-- ---------------------------------------------------------------------------

-- 10a. Add basis column (guarded)
SET @has_basis = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'QUARTERLY_FINANCIALS'
      AND COLUMN_NAME  = 'basis'
);
SET @sql_basis = IF(@has_basis = 0,
    'ALTER TABLE QUARTERLY_FINANCIALS
     ADD COLUMN basis VARCHAR(30) NULL
         COMMENT ''Segment reporting basis: as_reported_pre_recast | recast_2024-08 | as_reported''
         AFTER filed_date',
    'SELECT ''QUARTERLY_FINANCIALS.basis already exists'' AS note'
);
PREPARE _s18 FROM @sql_basis; EXECUTE _s18; DEALLOCATE PREPARE _s18;

-- 10b. Add source_url column (guarded)
SET @has_src = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'QUARTERLY_FINANCIALS'
      AND COLUMN_NAME  = 'source_url'
);
SET @sql_src = IF(@has_src = 0,
    'ALTER TABLE QUARTERLY_FINANCIALS
     ADD COLUMN source_url VARCHAR(500) NULL
         COMMENT ''URL of the Microsoft earnings release this row was sourced from''
         AFTER basis',
    'SELECT ''QUARTERLY_FINANCIALS.source_url already exists'' AS note'
);
PREPARE _s19 FROM @sql_src; EXECUTE _s19; DEALLOCATE PREPARE _s19;

-- 10c. Add UNIQUE KEY uq_qf_period (guarded)
SET @has_uq = (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'QUARTERLY_FINANCIALS'
      AND INDEX_NAME   = 'uq_qf_period'
);
SET @sql_uq = IF(@has_uq = 0,
    'ALTER TABLE QUARTERLY_FINANCIALS
     ADD UNIQUE KEY uq_qf_period (fiscal_year, fiscal_quarter, segment_id)',
    'SELECT ''uq_qf_period already exists on QUARTERLY_FINANCIALS'' AS note'
);
PREPARE _s20 FROM @sql_uq; EXECUTE _s20; DEALLOCATE PREPARE _s20;


-- ---------------------------------------------------------------------------
-- NOTE: After running this migration, re-run sql/07_grants.sql to apply
-- any updated grants (e.g. SELECT on GAMING_REVENUE_SERIES for business_analyst).
-- run_migrations.sh does NOT include migration files; apply manually.
-- ---------------------------------------------------------------------------


-- =============================================================================
-- Post-migration verification checklist (run manually after applying):
--
--   1. SHOW COLUMNS FROM STEAM_PLAYER LIKE 'avg_players';
--      → Type: decimal(12,2)
--
--   2. SHOW CREATE TABLE EVENT_WINDOW_MONTH\G
--      → Columns: event_id, avg_players DECIMAL(12,2), lift_ratio DECIMAL(10,4),
--                 month_offset SMALLINT NOT NULL
--      → PK: (event_id, month_offset)
--      → FK fk_ewm_event → BUSINESS_EVENT(event_id)
--      → No column named day_offset, no column named lift_ratio_new
--
--   3. SHOW COLUMNS FROM BUSINESS_EVENT LIKE 'steam_app_id';
--      → Type: int, Null: YES
--
--   4. SHOW CREATE TABLE GAMING_REVENUE_SERIES\G
--      → UNIQUE KEY uq_grs_period (fiscal_year, fiscal_quarter)
--
--   5. SELECT * FROM CONFIG WHERE config_key LIKE '%MONTHS%';
--      → 5 rows with value '3'
--
--   6. SHOW GRANTS FOR 'business_analyst';
--      → Includes GRANT SELECT ON margin_db.GAMING_REVENUE_SERIES
--
--   7. SHOW COLUMNS FROM QUARTERLY_FINANCIALS LIKE 'basis';
--      → Type: varchar(30), Null: YES
--
--   8. SHOW COLUMNS FROM QUARTERLY_FINANCIALS LIKE 'source_url';
--      → Type: varchar(500), Null: YES
--
--   9. SHOW INDEX FROM QUARTERLY_FINANCIALS WHERE Key_name = 'uq_qf_period';
--      → 3 rows (fiscal_year, fiscal_quarter, segment_id)
--
--  10. SELECT TABLE_NAME FROM information_schema.TABLES
--      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'EVENT_WINDOW_DAY';
--      → 0 rows (table should not exist)
-- =============================================================================
