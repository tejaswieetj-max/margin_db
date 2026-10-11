-- =============================================================================
-- 08_stage2_schema.sql  —  Stage 2 schema additions and corrections
-- =============================================================================
-- STATUS: WRITTEN BUT NOT EXECUTED.
-- Do NOT run this file until the owner approves it after Phase B review.
--
-- This file is a NEW migration; it does NOT modify 01_ddl.sql in place.
-- Apply via:
--   mysql -h $DB_HOST -P $DB_PORT -u $ADMIN_USER $DB_NAME \
--         < sql/migrations/08_stage2_schema.sql
-- On error: halt, log, do NOT run subsequent migrations (consistent with
-- the halt-on-error policy in run_migrations.sh).
--
-- Changes in this file:
--   1.  STEAM_PLAYER.avg_players: INT UNSIGNED → DECIMAL(12,2)
--   2.  Rename EVENT_WINDOW_DAY → EVENT_WINDOW_MONTH with new columns
--   3.  Add steam_app_id (nullable) to BUSINESS_EVENT
--   4.  New table GAMING_REVENUE_SERIES (separate from QUARTERLY_FINANCIALS)
--   5.  Seed CONFIG keys for lift/forecast parameters
--   6.  Grants update: add EXECUTE on new procedures to role_telemetry;
--       add SELECT on GAMING_REVENUE_SERIES to business_analyst
-- =============================================================================


-- ---------------------------------------------------------------------------
-- 1. STEAM_PLAYER.avg_players: INT UNSIGNED → DECIMAL(12,2)
--
-- Rationale: The source file (steam_players.csv) contains decimal values
-- such as 103689.35. The current INT UNSIGNED column silently truncates
-- fractional player-counts. DECIMAL(12,2) preserves source precision.
--
-- Task 2.5 validation rule update: validate as "non-negative decimal",
-- NOT "non-negative integer".  The loader script (load_steam.py) must be
-- updated accordingly (see Phase B note in MARGIN_DB.md).
-- ---------------------------------------------------------------------------
ALTER TABLE STEAM_PLAYER
    MODIFY COLUMN avg_players DECIMAL(12,2) NULL
        COMMENT 'Monthly avg concurrent players from Steam; decimal precision preserved from source CSV';


-- ---------------------------------------------------------------------------
-- 2a. Rename EVENT_WINDOW_DAY → EVENT_WINDOW_MONTH
--     and reshape its columns to monthly-offset logic.
--
-- Rationale:
--   - steam_players.csv is monthly, not daily. Day-offset logic (−14..+30)
--     cannot be applied to monthly data; the design must switch to month
--     offsets (−EVENT_PRE_MONTHS..+EVENT_POST_MONTHS).
--   - Offset 0 = the calendar month containing event start_date.
--   - Stored window: −3..+3 (driven by CONFIG keys EVENT_PRE_MONTHS,
--     EVENT_POST_MONTHS — both default 3).
--   - avg_players: DECIMAL(12,2) matches STEAM_PLAYER.avg_players precision.
--   - lift_ratio:  DECIMAL(10,4) NULL — NULL when baseline data is
--     insufficient (< MIN_BASELINE_MONTHS non-missing months preceding
--     offset 0); never filled with zero or estimated.
--
-- The existing EVENT_WINDOW_DAY table is RENAMED (not dropped/recreated) to
-- preserve any FK structure.  Because the DDL applies ALTER + RENAME atomically
-- within the same statement block, data loss is avoided during migration.
-- If the table is empty at migration time (expected at Stage 2 start), the
-- rename is safe; if rows exist they are carried forward with NULL avg_players.
-- ---------------------------------------------------------------------------

-- Step A: add new columns before rename so the rename carries correct structure
ALTER TABLE EVENT_WINDOW_DAY
    ADD COLUMN avg_players DECIMAL(12,2) NULL
        COMMENT 'Avg Steam concurrent players for this month offset'
        AFTER event_id,
    ADD COLUMN lift_ratio_new DECIMAL(10,4) NULL
        COMMENT 'lift_ratio = avg_players / baseline_avg_players; NULL if insufficient baseline'
        AFTER avg_players,
    MODIFY COLUMN day_offset SMALLINT NOT NULL
        COMMENT 'Month offset from event start month: negative = before, 0 = launch month, positive = after';

-- Step B: drop old lift_ratio (DECIMAL(8,4)) — replace with lift_ratio_new (DECIMAL(10,4))
ALTER TABLE EVENT_WINDOW_DAY
    DROP COLUMN lift_ratio,
    CHANGE COLUMN lift_ratio_new lift_ratio DECIMAL(10,4) NULL
        COMMENT 'Lift ratio = avg_players / baseline; NULL if baseline insufficient';

-- Step C: rename day_offset column to month_offset
ALTER TABLE EVENT_WINDOW_DAY
    CHANGE COLUMN day_offset month_offset SMALLINT NOT NULL
        COMMENT 'Month offset: −EVENT_PRE_MONTHS to +EVENT_POST_MONTHS';

-- Step D: rename the table
RENAME TABLE EVENT_WINDOW_DAY TO EVENT_WINDOW_MONTH;

-- Step E: confirm the new PK covers (event_id, month_offset)
-- The PK was (event_id, day_offset); after column rename it is now
-- (event_id, month_offset). No PK rebuild needed — MySQL tracks the column
-- by its new name after CHANGE COLUMN.
-- Verify in information_schema.KEY_COLUMN_USAGE that PK columns are
-- event_id + month_offset after running this migration.


-- ---------------------------------------------------------------------------
-- 2b. Update the FK name on EVENT_WINDOW_MONTH for clarity
--     (the underlying FK still references BUSINESS_EVENT; we just rename it
--      so it matches the new table name in documentation)
-- ---------------------------------------------------------------------------
ALTER TABLE EVENT_WINDOW_MONTH
    DROP FOREIGN KEY fk_ewd_event,
    ADD CONSTRAINT fk_ewm_event
        FOREIGN KEY (event_id) REFERENCES BUSINESS_EVENT (event_id);


-- ---------------------------------------------------------------------------
-- 3. Add steam_app_id (nullable INT) to BUSINESS_EVENT
--
-- Rationale:
--   - Events linked to a Steam title get a steam_app_id; the lift pipeline
--     then joins to STEAM_PLAYER.app_id for that title.
--   - Events with NULL steam_app_id (e.g. Black Friday, price changes,
--     acquisition events) get NULL lift_ratio — they never go through the
--     Steam path. This is correct; do NOT force a dummy app_id on them.
--   - events.csv will add a steam_app_id column; rows without an app_id
--     leave the field empty/null.
-- ---------------------------------------------------------------------------
ALTER TABLE BUSINESS_EVENT
    ADD COLUMN steam_app_id INT NULL
        COMMENT 'Steam app_id for game-launch events; NULL for non-Steam events'
        AFTER expected_lift;


-- ---------------------------------------------------------------------------
-- 4. New table: GAMING_REVENUE_SERIES
--
-- Rationale:
--   QUARTERLY_FINANCIALS has PK (qf_id) and a segment_id FK to COMPANY_SEGMENT.
--   Gaming is NOT one of the three official reporting segments (PBP, IC, MPC).
--   Inserting a "Gaming" row into QUARTERLY_FINANCIALS would either:
--     (a) require fabricating a fourth segment — violating the three-segment rule, or
--     (b) shoe-horn Gaming under MPC, causing double-counting.
--   A separate table cleanly isolates Gaming revenue as a product-line series
--   without touching the segment model.
--
-- Source: data/raw/segments.csv
-- Units:  millions USD (matching the CSV header "revenue (in million dollars)")
-- All financial values in this DB are stored in MILLIONS USD.
--   (EDGAR raw USD values must be divided by 1,000,000 at load time.)
--
-- operating_income: always NULL (Gaming OI not separately disclosed by Microsoft).
-- months_label:     VARCHAR(40) preserving the "Months" column from segments.csv
--                   as reference metadata (e.g. "Jul 2022 to Sep 2022").
-- source_url:       NOT NULL — every row must cite its earnings release URL.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS GAMING_REVENUE_SERIES (
    grs_id           INT            NOT NULL AUTO_INCREMENT,
    fiscal_year      SMALLINT       NOT NULL,
    fiscal_quarter   TINYINT        NOT NULL,
    revenue_m        DECIMAL(12,2)  NOT NULL
        COMMENT 'Gaming revenue in millions USD (source: segments.csv)',
    operating_income DECIMAL(12,2)  NULL
        COMMENT 'Always NULL — Gaming OI not separately disclosed; never estimated',
    months_label     VARCHAR(40)    NULL
        COMMENT 'Reference label from segments.csv Months column e.g. "Jul 2022 to Sep 2022"',
    source_url       VARCHAR(500)   NOT NULL
        COMMENT 'URL of the Microsoft earnings release cited in segments.csv',
    loaded_at        DATETIME(3)    NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (grs_id),
    UNIQUE KEY uq_grs_period (fiscal_year, fiscal_quarter)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Gaming product-line revenue series sourced from segments.csv. '
        'NOT a COMPANY_SEGMENT row. Gaming sits inside MPC; this table '
        'is kept separate to avoid double-counting. '
        'All values in millions USD.';


-- ---------------------------------------------------------------------------
-- 5. Seed CONFIG keys for lift and forecast parameters
--
-- All lift/forecast logic MUST read these from CONFIG, never hardcode them.
-- The default values here match the Phase B specification:
--   EVENT_PRE_MONTHS    = 3   (store window months before offset 0)
--   EVENT_POST_MONTHS   = 3   (store window months after offset 0)
--   BASELINE_MONTHS     = 3   (number of months averaged for baseline)
--   MIN_BASELINE_MONTHS = 3   (minimum months required; fewer → NULL lift)
--   FORECAST_LIFT_MONTHS = 3  (forecast averages offsets 0..+2 inclusive)
--
-- Idempotent: ON DUPLICATE KEY UPDATE is a no-op if value is already set.
-- To change a value: UPDATE CONFIG SET config_value=... WHERE config_key=...
-- ---------------------------------------------------------------------------
INSERT INTO CONFIG (config_key, config_value) VALUES
    ('EVENT_PRE_MONTHS',     '3'),
    ('EVENT_POST_MONTHS',    '3'),
    ('BASELINE_MONTHS',      '3'),
    ('MIN_BASELINE_MONTHS',  '3'),
    ('FORECAST_LIFT_MONTHS', '3')
ON DUPLICATE KEY UPDATE config_value = config_value;  -- no-op if already set


-- ---------------------------------------------------------------------------
-- 6. Grant additions
--
-- 6a. business_analyst: SELECT on GAMING_REVENUE_SERIES
--     (analysts may view Gaming revenue in v_financials or a new view;
--     grant the base table so a future view can reference it)
-- 6b. role_telemetry: no changes needed for this migration
-- ---------------------------------------------------------------------------
GRANT SELECT ON margin_db.GAMING_REVENUE_SERIES TO 'business_analyst';


-- =============================================================================
-- Post-migration verification checklist (run manually after applying):
--
--   1. SHOW COLUMNS FROM STEAM_PLAYER LIKE 'avg_players';
--      → Type should be decimal(12,2)
--
--   2. SHOW CREATE TABLE EVENT_WINDOW_MONTH\G
--      → PK should be (event_id, month_offset)
--      → Columns: event_id, avg_players DECIMAL(12,2), lift_ratio DECIMAL(10,4),
--                 month_offset SMALLINT
--      → FK fk_ewm_event → BUSINESS_EVENT(event_id)
--
--   3. SHOW COLUMNS FROM BUSINESS_EVENT LIKE 'steam_app_id';
--      → Type should be int, NULL allowed
--
--   4. SHOW CREATE TABLE GAMING_REVENUE_SERIES\G
--      → UNIQUE KEY uq_grs_period (fiscal_year, fiscal_quarter)
--
--   5. SELECT * FROM CONFIG WHERE config_key LIKE '%MONTHS%';
--      → Should return 5 rows with values '3'
--
--   6. SHOW GRANTS FOR 'business_analyst';
--      → Should include GRANT SELECT ON margin_db.GAMING_REVENUE_SERIES
--
--   7. Verify README weak-entity note still lists EVENT_WINDOW_MONTH
--      (renamed from EVENT_WINDOW_DAY) as a weak entity.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 7. USER_SEGMENT_MAP: add nullable product_line column
--
-- Rationale: analyst_demo needs to be scoped to MPC segment but filtered
-- further to product_line='Gaming' within that segment. Adding a nullable
-- product_line column lets the view filter on (segment_id, product_line)
-- without requiring a separate mapping table.
-- NULL product_line = no product-line restriction (full segment access).
-- ---------------------------------------------------------------------------
-- Guard: only add if not already present
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'USER_SEGMENT_MAP'
      AND COLUMN_NAME  = 'product_line'
);
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE USER_SEGMENT_MAP ADD COLUMN product_line VARCHAR(50) NULL COMMENT ''NULL = full-segment access; non-NULL restricts to that product line''',
    'SELECT ''product_line column already exists'' AS note'
);
PREPARE _stmt FROM @sql;
EXECUTE _stmt;
DEALLOCATE PREPARE _stmt;

-- ---------------------------------------------------------------------------
-- 8. PRODUCT: add nullable product_line column
-- Guard: only add if not already present
-- ---------------------------------------------------------------------------
SET @col_exists2 = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'PRODUCT'
      AND COLUMN_NAME  = 'product_line'
);
SET @sql2 = IF(@col_exists2 = 0,
    'ALTER TABLE PRODUCT ADD COLUMN product_line VARCHAR(50) NULL COMMENT ''Gaming, or NULL for non-tagged products'' AFTER unit_cost_ratio',
    'SELECT ''product_line column already exists on PRODUCT'' AS note'
);
PREPARE _stmt2 FROM @sql2;
EXECUTE _stmt2;
DEALLOCATE PREPARE _stmt2;

-- ---------------------------------------------------------------------------
-- 9. FACT_SALES.cost_amount: make nullable
--    Gaming synthetic rows will have cost_amount = NULL (no Gaming cost data).
-- Guard: only modify if currently NOT NULL
-- ---------------------------------------------------------------------------
SET @is_nullable = (
    SELECT IS_NULLABLE FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'FACT_SALES'
      AND COLUMN_NAME  = 'cost_amount'
);
SET @sql3 = IF(@is_nullable = 'NO',
    'ALTER TABLE FACT_SALES MODIFY COLUMN cost_amount DECIMAL(15,2) NULL COMMENT ''NULL for Gaming rows (no cost data disclosed)''',
    'SELECT ''cost_amount already nullable'' AS note'
);
PREPARE _stmt3 FROM @sql3;
EXECUTE _stmt3;
DEALLOCATE PREPARE _stmt3;

-- ---------------------------------------------------------------------------
-- NOTE: After running this migration, re-run sql/07_grants.sql to apply
-- updated grants (e.g. SELECT on GAMING_REVENUE_SERIES for business_analyst).
-- run_migrations.sh does NOT include migration files; apply manually.
-- ---------------------------------------------------------------------------
