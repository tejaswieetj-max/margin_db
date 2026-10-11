-- =============================================================================
-- 09_fix_segments.sql  —  Segment fix: PBP/IC/MPC, remove Gaming segment row
-- =============================================================================
-- STATUS: FULLY IDEMPOTENT — safe to re-run in any starting state.
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
--   2. Rename 'Productivity & Business Cloud' -> 'Productivity and Business Processes'
--   3. Insert 'Intelligent Cloud' (so all three segments exist for step 6)
--   4. Retag Gaming PRODUCT rows: segment=MPC, product_line='Gaming'
--   5. Delete ALL USER_SEGMENT_MAP rows that reference the Gaming segment_id
--      (any db_user — not just analyst_demo)
--   6. Insert correct USER_SEGMENT_MAP rows for analyst_demo and analyst_all
--   7. Delete the Gaming COMPANY_SEGMENT row (FK-safe: no PRODUCT or USM rows
--      reference it after steps 4 and 5)
--
-- No GRANT statements in this file. Re-run 07_grants.sql after this migration.
--
-- Idempotency notes:
--   Steps 1a/1b/1c — information_schema guards; session vars prefixed _09_
--     to avoid collision with identically-named vars in 08_stage2_schema.sql
--     if both files are sourced in the same mysql session.
--   Step 2  — UPDATE on COMPANY_SEGMENT; 0 rows matched if already renamed.
--   Step 3  — INSERT IGNORE; safe on re-run.
--   Step 4  — UPDATE joins on Gaming segment; returns 0 rows after Step 7
--     has run (Gaming row deleted); safe no-op on re-run.
--   Step 5  — DELETE WHERE segment_id IN (subquery for 'Gaming'); subquery
--     returns no rows once Gaming segment is deleted; safe no-op on re-run.
--   Step 6a — INSERT IGNORE for analyst_demo; safe on re-run.
--   Step 6b — DELETE + INSERT IGNORE for analyst_all; safe on re-run.
--   Step 7  — DELETE WHERE name='Gaming'; 0 rows on re-run. Safe.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Step 1a. Add product_line to USER_SEGMENT_MAP (guard)
-- Variable names prefixed _09_ to avoid session-variable collisions with 08.
-- ---------------------------------------------------------------------------
SET @_09_col_usm = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'USER_SEGMENT_MAP'
      AND COLUMN_NAME  = 'product_line'
);
SET @_09_sql_usm = IF(@_09_col_usm = 0,
    'ALTER TABLE USER_SEGMENT_MAP ADD COLUMN product_line VARCHAR(50) NULL COMMENT ''NULL = full-segment; non-NULL restricts to product line''',
    'SELECT ''USM product_line already exists'' AS note'
);
PREPARE _s09a FROM @_09_sql_usm; EXECUTE _s09a; DEALLOCATE PREPARE _s09a;

-- ---------------------------------------------------------------------------
-- Step 1b. Add product_line to PRODUCT (guard)
-- ---------------------------------------------------------------------------
SET @_09_col_prod = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'PRODUCT'
      AND COLUMN_NAME  = 'product_line'
);
SET @_09_sql_prod = IF(@_09_col_prod = 0,
    'ALTER TABLE PRODUCT ADD COLUMN product_line VARCHAR(50) NULL AFTER unit_cost_ratio',
    'SELECT ''PRODUCT product_line already exists'' AS note'
);
PREPARE _s09b FROM @_09_sql_prod; EXECUTE _s09b; DEALLOCATE PREPARE _s09b;

-- ---------------------------------------------------------------------------
-- Step 1c. Make FACT_SALES.cost_amount nullable (guard)
-- ---------------------------------------------------------------------------
SET @_09_col_cs = (
    SELECT IS_NULLABLE FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME   = 'FACT_SALES'
      AND COLUMN_NAME  = 'cost_amount'
);
SET @_09_sql_cs = IF(@_09_col_cs = 'NO',
    'ALTER TABLE FACT_SALES MODIFY COLUMN cost_amount DECIMAL(15,2) NULL',
    'SELECT ''cost_amount already nullable'' AS note'
);
PREPARE _s09c FROM @_09_sql_cs; EXECUTE _s09c; DEALLOCATE PREPARE _s09c;

-- ---------------------------------------------------------------------------
-- Step 2. Rename 'Productivity & Business Cloud'
--         -> 'Productivity and Business Processes'
-- Must run before Step 6 so the correct segment name exists when
-- analyst_all rows are inserted.
-- Idempotent: UPDATE matches 0 rows if already renamed.
-- ---------------------------------------------------------------------------
UPDATE COMPANY_SEGMENT
SET name = 'Productivity and Business Processes'
WHERE name = 'Productivity & Business Cloud';

-- ---------------------------------------------------------------------------
-- Step 3. Insert 'Intelligent Cloud' if not already present.
-- INSERT IGNORE is idempotent.
-- ---------------------------------------------------------------------------
INSERT IGNORE INTO COMPANY_SEGMENT (name) VALUES ('Intelligent Cloud');

-- ---------------------------------------------------------------------------
-- Step 4. Retag Gaming PRODUCT rows: segment=MPC, product_line='Gaming'
-- Must happen BEFORE deleting the Gaming COMPANY_SEGMENT row in Step 7.
-- Idempotent: after Gaming segment is deleted (Step 7), the JOIN on
-- g.name='Gaming' finds 0 rows; UPDATE is a safe no-op on re-run.
-- ---------------------------------------------------------------------------
UPDATE PRODUCT p
JOIN   COMPANY_SEGMENT g ON p.segment_id = g.segment_id AND g.name = 'Gaming'
JOIN   COMPANY_SEGMENT m ON m.name = 'More Personal Computing'
SET    p.segment_id   = m.segment_id,
       p.product_line = 'Gaming'
WHERE  p.product_line IS NULL OR p.product_line = 'Gaming';

-- ---------------------------------------------------------------------------
-- Step 5. Delete ALL USER_SEGMENT_MAP rows that reference the Gaming
-- segment_id (any db_user — not just analyst_demo).
-- This must happen BEFORE deleting the Gaming COMPANY_SEGMENT row (Step 7)
-- to avoid leaving orphaned foreign-key rows.
-- The subquery resolves the Gaming segment_id by name; if Gaming is already
-- deleted (re-run), the subquery returns no rows, making the DELETE a no-op.
-- Idempotent: DELETE on 0 rows is not an error in MySQL.
-- ---------------------------------------------------------------------------
DELETE FROM USER_SEGMENT_MAP
WHERE segment_id IN (
    SELECT segment_id FROM COMPANY_SEGMENT WHERE name = 'Gaming'
);

-- ---------------------------------------------------------------------------
-- Step 6. Insert correct USER_SEGMENT_MAP rows for analyst_demo and
-- analyst_all. All three segments now exist (Steps 2+3 ensured it).
-- ---------------------------------------------------------------------------

-- 6a. analyst_demo -> MPC segment, product_line='Gaming'
--     INSERT IGNORE handles re-runs safely.
INSERT IGNORE INTO USER_SEGMENT_MAP (db_user, segment_id, product_line)
    SELECT 'analyst_demo', segment_id, 'Gaming'
    FROM   COMPANY_SEGMENT
    WHERE  name = 'More Personal Computing';

-- 6b. analyst_all -> PBP, IC, MPC with product_line=NULL (full segment access).
--     DELETE first to clear any stale product_line values left by prior runs,
--     then re-insert. DELETE on 0 rows is safe; INSERT IGNORE handles duplicates.
DELETE FROM USER_SEGMENT_MAP WHERE db_user = 'analyst_all';
INSERT IGNORE INTO USER_SEGMENT_MAP (db_user, segment_id, product_line)
    SELECT 'analyst_all', segment_id, NULL
    FROM   COMPANY_SEGMENT
    WHERE  name IN ('Productivity and Business Processes', 'Intelligent Cloud',
                    'More Personal Computing');

-- ---------------------------------------------------------------------------
-- Step 7. Delete 'Gaming' COMPANY_SEGMENT row.
-- FK-safe at this point:
--   - No PRODUCT rows reference Gaming (Step 4 retagged them to MPC).
--   - No USER_SEGMENT_MAP rows reference Gaming (Step 5 deleted them).
-- Also safe for QUARTERLY_FINANCIALS: Gaming rows should not exist there
-- (real Gaming revenue lives in GAMING_REVENUE_SERIES).
-- Idempotent: DELETE WHERE name='Gaming' returns 0 rows if already gone;
-- MySQL does not error on a 0-row DELETE.
-- ---------------------------------------------------------------------------
DELETE FROM COMPANY_SEGMENT WHERE name = 'Gaming';

-- ---------------------------------------------------------------------------
-- Post-migration verification checklist:
--
--   1. SELECT name FROM COMPANY_SEGMENT ORDER BY name;
--      Should return: Intelligent Cloud, More Personal Computing,
--                     Productivity and Business Processes
--
--   2. SELECT COUNT(*) FROM PRODUCT WHERE product_line='Gaming';
--      Should return 40
--
--   3. SELECT COUNT(*) FROM PRODUCT p
--      JOIN COMPANY_SEGMENT g ON p.segment_id=g.segment_id
--      WHERE g.name='Gaming';
--      Should return 0
--
--   4. SELECT * FROM USER_SEGMENT_MAP;
--      analyst_demo: 1 row  (MPC, product_line='Gaming')
--      analyst_all:  3 rows (PBP/IC/MPC, product_line=NULL each)
--      No rows with segment_id pointing to a deleted Gaming segment.
--
--   5. After verifying, re-run sql/07_grants.sql to apply updated grants.
-- ---------------------------------------------------------------------------
