-- =============================================================================
-- 09_fix_segments.sql  —  Segment fix: PBP/IC/MPC, remove Gaming segment row
-- =============================================================================
-- STATUS: WRITTEN BUT NOT EXECUTED.
-- Apply ONLY on schemas that were previously seeded with the old segment names
-- ('Gaming', 'Productivity & Business Cloud', 'More Personal Computing').
-- Fresh installs using the updated 06_seed_reference.sql do NOT need this file.
--
-- IMPORTANT: run_migrations.sh does NOT include this file.
-- Apply manually:
--   mysql -h $DB_HOST -P $DB_PORT -u $ADMIN_USER $DB_NAME \
--         < sql/migrations/09_fix_segments.sql
-- On error: halt, do NOT run subsequent migrations.
--
-- Order of operations (must be in this order to avoid FK violations):
--   1. Add product_line to USER_SEGMENT_MAP and PRODUCT (if 08 not yet run)
--   2. Retag Gaming PRODUCT rows to segment=MPC, product_line='Gaming'
--   3. Remap analyst_demo in USER_SEGMENT_MAP to MPC+Gaming
--   4. Delete Gaming USER_SEGMENT_MAP rows
--   5. Rename 'Productivity & Business Cloud' -> 'Productivity and Business Processes'
--   6. Insert 'Intelligent Cloud'
--   7. Delete 'Gaming' segment row (safe now — no PRODUCT rows reference it)
--
-- No GRANT statements in this file. Re-run 07_grants.sql after this migration.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Step 1a. Add product_line to USER_SEGMENT_MAP (guard)
-- ---------------------------------------------------------------------------
SET @col_usm = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'USER_SEGMENT_MAP'
      AND COLUMN_NAME  = 'product_line'
);
SET @sql_usm = IF(@col_usm = 0,
    'ALTER TABLE USER_SEGMENT_MAP ADD COLUMN product_line VARCHAR(50) NULL COMMENT ''NULL = full-segment; non-NULL restricts to product line''',
    'SELECT ''USM product_line already exists'' AS note'
);
PREPARE _s FROM @sql_usm; EXECUTE _s; DEALLOCATE PREPARE _s;

-- ---------------------------------------------------------------------------
-- Step 1b. Add product_line to PRODUCT (guard)
-- ---------------------------------------------------------------------------
SET @col_prod = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'PRODUCT'
      AND COLUMN_NAME  = 'product_line'
);
SET @sql_prod = IF(@col_prod = 0,
    'ALTER TABLE PRODUCT ADD COLUMN product_line VARCHAR(50) NULL AFTER unit_cost_ratio',
    'SELECT ''PRODUCT product_line already exists'' AS note'
);
PREPARE _s2 FROM @sql_prod; EXECUTE _s2; DEALLOCATE PREPARE _s2;

-- ---------------------------------------------------------------------------
-- Step 1c. Make FACT_SALES.cost_amount nullable (guard)
-- ---------------------------------------------------------------------------
SET @col_cs = (
    SELECT IS_NULLABLE FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'FACT_SALES'
      AND COLUMN_NAME  = 'cost_amount'
);
SET @sql_cs = IF(@col_cs = 'NO',
    'ALTER TABLE FACT_SALES MODIFY COLUMN cost_amount DECIMAL(15,2) NULL',
    'SELECT ''cost_amount already nullable'' AS note'
);
PREPARE _s3 FROM @sql_cs; EXECUTE _s3; DEALLOCATE PREPARE _s3;

-- ---------------------------------------------------------------------------
-- Step 2. Retag Gaming PRODUCT rows: segment=MPC, product_line='Gaming'
-- (Must happen BEFORE deleting the Gaming segment row)
-- ---------------------------------------------------------------------------
UPDATE PRODUCT p
JOIN   COMPANY_SEGMENT g ON p.segment_id = g.segment_id AND g.name = 'Gaming'
JOIN   COMPANY_SEGMENT m ON m.name = 'More Personal Computing'
SET    p.segment_id   = m.segment_id,
       p.product_line = 'Gaming'
WHERE  p.product_line IS NULL OR p.product_line = 'Gaming';

-- Verify: no PRODUCT row should reference Gaming after this
-- SELECT COUNT(*) FROM PRODUCT p JOIN COMPANY_SEGMENT g
--   ON p.segment_id=g.segment_id WHERE g.name='Gaming';  -- expect 0

-- ---------------------------------------------------------------------------
-- Step 3. Remap analyst_demo: DELETE old Gaming mapping, INSERT MPC+Gaming
-- ---------------------------------------------------------------------------
DELETE FROM USER_SEGMENT_MAP
WHERE db_user = 'analyst_demo';

INSERT IGNORE INTO USER_SEGMENT_MAP (db_user, segment_id, product_line)
    SELECT 'analyst_demo', segment_id, 'Gaming'
    FROM   COMPANY_SEGMENT
    WHERE  name = 'More Personal Computing';

-- analyst_all: set product_line = NULL for all rows (full segment access)
UPDATE USER_SEGMENT_MAP SET product_line = NULL WHERE db_user = 'analyst_all';
-- Re-insert analyst_all for PBP and IC if missing
INSERT IGNORE INTO USER_SEGMENT_MAP (db_user, segment_id, product_line)
    SELECT 'analyst_all', segment_id, NULL
    FROM   COMPANY_SEGMENT
    WHERE  name IN ('Productivity and Business Processes', 'Intelligent Cloud',
                    'More Personal Computing');

-- ---------------------------------------------------------------------------
-- Step 4. Rename 'Productivity & Business Cloud' -> 'Productivity and Business Processes'
-- ---------------------------------------------------------------------------
UPDATE COMPANY_SEGMENT
SET name = 'Productivity and Business Processes'
WHERE name = 'Productivity & Business Cloud';

-- ---------------------------------------------------------------------------
-- Step 5. Insert 'Intelligent Cloud' (if not already present)
-- ---------------------------------------------------------------------------
INSERT IGNORE INTO COMPANY_SEGMENT (name) VALUES ('Intelligent Cloud');

-- ---------------------------------------------------------------------------
-- Step 6. Delete 'Gaming' segment row
-- Safe now because Step 2 retagged all PRODUCT rows away from Gaming.
-- Also safe for QUARTERLY_FINANCIALS: Gaming rows should not exist there
-- (real Gaming revenue lives in GAMING_REVENUE_SERIES).
-- Check: SELECT COUNT(*) FROM QUARTERLY_FINANCIALS qf
--        JOIN COMPANY_SEGMENT cs ON qf.segment_id=cs.segment_id
--        WHERE cs.name='Gaming';  -- expect 0 before running this step
-- ---------------------------------------------------------------------------
DELETE FROM COMPANY_SEGMENT WHERE name = 'Gaming';

-- ---------------------------------------------------------------------------
-- Post-migration verification checklist:
--
--   1. SELECT name FROM COMPANY_SEGMENT ORDER BY name;
--      → Should return: Intelligent Cloud, More Personal Computing,
--                       Productivity and Business Processes
--
--   2. SELECT COUNT(*) FROM PRODUCT WHERE product_line='Gaming';
--      → Should return 40
--
--   3. SELECT COUNT(*) FROM PRODUCT p
--      JOIN COMPANY_SEGMENT g ON p.segment_id=g.segment_id
--      WHERE g.name='Gaming';
--      → Should return 0
--
--   4. SELECT * FROM USER_SEGMENT_MAP;
--      → analyst_demo: 1 row (MPC, product_line='Gaming')
--      → analyst_all: 3 rows (PBP/IC/MPC, product_line=NULL each)
--
--   5. After verifying, re-run sql/07_grants.sql to apply updated grants.
-- ---------------------------------------------------------------------------
