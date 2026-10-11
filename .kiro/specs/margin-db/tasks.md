# Implementation Plan: Margin-DB

## Overview

Six-stage implementation plan following the staged development process (Requirement 22). Each stage ends with a gate that must pass before the next stage begins. Tasks marked `*` are optional and may be skipped for a faster MVP. Each stage ends with a git commit.

---

## Tasks

- [x] 0. Stage 0 — Environment

  - [x] 0.0 HUMAN STEP — Create database and ADMIN_USER
    - **HUMAN STEP**: Before any code is written, log in to MySQL as root and execute:
      ```sql
      CREATE DATABASE IF NOT EXISTS margin_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
      CREATE USER 'admin_user'@'localhost' IDENTIFIED BY '<password>';
      GRANT ALL ON margin_db.* TO 'admin_user'@'localhost' WITH GRANT OPTION;
      GRANT CREATE USER, CREATE ROLE, ROLE_ADMIN ON *.* TO 'admin_user'@'localhost';
      GRANT SELECT ON performance_schema.* TO 'admin_user'@'localhost' WITH GRANT OPTION;
      SET GLOBAL event_scheduler = ON;
      SET GLOBAL local_infile = 1;
      ```
    - Update `.env`: `ADMIN_USER=admin_user`, `ADMIN_PASSWORD=<chosen password>`, `DB_NAME=margin_db`
    - Agent must stop and wait for user confirmation before proceeding to 0.1
    - _Requirements: 22.1, 22.4_

  - [x] 0.1 Create repository skeleton and Python virtual environment
    - Create directories: `sql/`, `etl/`, `backend/app/routers/`, `backend/app/models/`, `frontend/src/`, `tests/`, `docs/`, `data/raw/`, `data/clean/`
    - Write `.gitignore`: ignore `.env`, `__pycache__/`, `*.pyc`, `node_modules/`, `data/clean/`, `data/raw/msft_facts.json`, `.venv/`, `dist/`, `*.sql`; add `!data/raw/events.csv` and `!data/raw/segments.csv` to force-include those committed files
    - Write `.env.example` with all required variables and a comment + placeholder for each: `DB_HOST`, `DB_PORT`, `DB_NAME`, `ADMIN_USER`, `ADMIN_PASSWORD`, `SVC_TELEMETRY_USER`, `SVC_TELEMETRY_PASSWORD`, `SEC_USER_AGENT`, `JWT_SECRET`, `DB_ALLOCATED_MB`, `DEMO_ANALYST_PASSWORD`, `DEMO_ANALYST_ALL_PASSWORD`, `DEMO_ENGINEER_PASSWORD`, `SYNTHETIC_SEED`, `ANALYST_DEMO_USERNAME=analyst_demo`, `ANALYST_ALL_USERNAME=analyst_all`, `ENGINEER_DEMO_USERNAME=engineer_demo`
    - Write `backend/requirements.txt` with pinned versions: `fastapi`, `"uvicorn[standard]"`, `pyodbc`, `pyjwt`, `pydantic`, `pytest`, `requests`, `pandas`, `numpy`, `python-dotenv`
    - Write `etl/requirements.txt` with pinned versions: `mysql-connector-python`, `python-dotenv`, `pytrends`, `hypothesis`, `pandas`, `numpy`
    - Initialize venv at `.venv`; document activation command in README
    - _Requirements: 20.1, 20.2, 20.3_

  - [x] 0.2 Scaffold React + Vite frontend
    - Run `npm create vite@latest frontend -- --template react`
    - Install: `axios`, `recharts`, `react-router-dom`
    - Create stub files: `src/api/client.js`, `src/components/ProtectedRoute.jsx`, `src/components/RoleRoute.jsx`, `src/hooks/useAuth.js`, `src/hooks/useApi.js`
    - Verify `npm run build` exits 0
    - _Requirements: 19.1, 19.2, 19.3_

  - [x] 0.3 Write test_odbc.py — Stage 0 version (connect + SELECT ? only)
    - `test_odbc_connection`: connect within 10 seconds; assert MySQL version string starts with "8."
    - `test_parameterized_query`: run `SELECT ?` with a string binding; assert result matches input; does NOT query COMPANY_SEGMENT (table not yet created at Stage 0)
    - Handle `pyodbc.InterfaceError` (driver not found) by printing the driver name and failing cleanly
    - Read all connection parameters from environment variables
    - Note in file: COMPANY_SEGMENT query and stored-procedure test moved to Stage 1 gate (task 1.10)
    - _Requirements: 18.4, 18.6_

  - [x] 0.4 Write README.md
    - Prerequisites section: Python 3.12, Node 18, MySQL 8.0.18+, MySQL ODBC 8.0 Unicode Driver, unixODBC — with minimum versions
    - Setup steps referencing `.env.example`; venv activation; `npm install`
    - Stage-by-stage run instructions; demo loop instructions with concrete commands in each section
    - Note on MySQL stored procedure syntax vs Oracle PL/SQL: "MySQL uses `DELIMITER` to define multi-statement procedures; there is no `DECLARE...EXCEPTION` block, no `DBMS_*` packages, no `:=` operator, and no `RAISE_APPLICATION_ERROR`. Error handling uses `DECLARE ... HANDLER FOR SQLEXCEPTION BEGIN ... END`."
    - `uname -m` platform notes
    - Weak entities note: "This schema has four weak entities: ORDER_ITEM (owner ORDERS), EVENT_WINDOW_MONTH (owner BUSINESS_EVENT), QUERY_PLAN_NODE (owner QUERY_EXECUTION), FORECAST_LINE (owner FORECAST)"
    - _Requirements: 20.3, 20.5_

  - [x] 0.5 Stage 0 gate
    - Run `pytest tests/test_odbc.py::test_odbc_connection tests/test_odbc.py::test_parameterized_query` — only these two tests; both must pass
    - Record MySQL version, `performance_schema` ON/OFF, and host architecture (`uname -m`) in `docs/stage_0_report.md`
    - **git commit**: `git add -A && git commit -m "Stage 0: environment"`
    - _Requirements: 22.1, 22.2_

- [ ] 1. Stage 1 — Schema

  - [x] 1.1 Write 01_ddl.sql — all 24 tables plus CONFIG
    - All 24 tables in dependency order using `CREATE TABLE IF NOT EXISTS` only — no DROP TABLE statements in this file
    - `CONFIG` table: `config_key VARCHAR(64) PRIMARY KEY`, `config_value VARCHAR(255) NOT NULL`
    - `FACT_SALES`: composite PK `(sale_id, sale_date)`; `PARTITION BY RANGE (TO_DAYS(sale_date))` with 20 named quarterly partitions `p_FY23Q1` through `p_FY27Q4` (boundary dates 2022-10-01, 2023-01-01, … 2027-07-01) plus `p_catch MAXVALUE`; `is_synthetic BOOLEAN NOT NULL DEFAULT TRUE`; NO FK constraints; NO secondary indexes
    - `INDEX_RECOMMENDATION`: include `index_name VARCHAR(128)` column
    - `DB_SIZE_SNAPSHOT`: `is_synthetic BOOLEAN NOT NULL DEFAULT FALSE`; `UNIQUE KEY (snapped_at)` to prevent duplicate daily snapshots
    - FK constraints on all tables except `FACT_SALES`
    - Four weak entities with composite PKs: `ORDER_ITEM (order_id, line_no)`, `EVENT_WINDOW_MONTH (event_id, month_offset)`, `QUERY_PLAN_NODE (qe_id, node_id)`, `FORECAST_LINE (forecast_id, line_no)`
    - NOTE: AZURE_VM table added in migration 10a (not in 01_ddl.sql)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 14.1_

  - [x] 1.1b Write sql/reset_db.sql — destructive reset script
    - Drops all 24 tables in reverse dependency order and all views, then recreates the database
    - Requires interactive confirmation: script must prompt "Type YES to reset margin_db" and halt if input != "YES"
    - Must NOT be called by run_migrations.sh; add a prominent comment warning at the top of the file
    - _Requirements: 3.1_

  - [x] 1.2 Write 02_roles.sql — role objects and user accounts only
    - `CREATE ROLE IF NOT EXISTS`: `business_analyst`, `db_engineer`, `role_telemetry`
    - `CREATE USER IF NOT EXISTS`: `analyst_demo`, `analyst_all`, `engineer_demo`, `svc_telemetry`
    - Password placeholders: `IDENTIFIED BY '${DEMO_ANALYST_PASSWORD}'` etc. (envsubst substitutes at runtime)
    - NO GRANT statements; NO SET DEFAULT ROLE — those belong only in `07_grants.sql`
    - _Requirements: 2.8, 3.1, 3.9_

  - [x] 1.3 Write 03_views.sql — all 8 views with SQL SECURITY DEFINER
    - All views: `DROP VIEW IF EXISTS` + `CREATE VIEW ... SQL SECURITY DEFINER AS ...`
    - `v_financials`: QUARTERLY_FINANCIALS JOIN COMPANY_SEGMENT; segment filter via USER_SEGMENT_MAP
    - `v_events`: BUSINESS_EVENT; no segment filter; full event details
    - `v_forecast_business`: FORECAST JOIN FORECAST_LINE WHERE kind='business'; no segment filter
    - `v_my_queries`: QUERY_EXECUTION LEFT JOIN INDEX_RECOMMENDATION; filtered to `db_user = SUBSTRING_INDEX(USER(),'@',1)` — this is a per-user filter, NOT a segment filter
    - `v_events_min`: BUSINESS_EVENT; only columns: name, start_date, end_date, expected_lift; no revenue columns
    - `v_forecast_database`: FORECAST JOIN FORECAST_LINE WHERE kind='database'
    - `v_vm_workload`: ROW-LEVEL on AZURE_VM (vm_id, subscription_id, deployment_id, trace_day, created_at, deleted_at, max_cpu, avg_cpu, p95_max_cpu, vm_category, vcpu_bucket, memory_bucket); no segment filter; AZURE_VM not aliased to keep EXPLAIN ANALYZE output unambiguous
    - `v_partitions`: `information_schema.PARTITIONS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='AZURE_VM'`
    - Segment filter pattern for v_financials only: `WHERE segment_id IN (SELECT segment_id FROM USER_SEGMENT_MAP WHERE db_user = SUBSTRING_INDEX(USER(),'@',1))`
    - _Requirements: 2.1, 2.3, 10.1, 16.2_

  - [x] 1.4 Write 04_routines.sql — stubs for all stored procedures
    - All procedures: `DROP PROCEDURE IF EXISTS` + `CREATE PROCEDURE ... SQL SECURITY DEFINER`
    - Minimal stub bodies (SELECT NULL or SET p_out=NULL); correct signatures must be exact:
      - `sp_margin_decomposition(IN p_segment VARCHAR(50), IN p_period_a VARCHAR(10), IN p_period_b VARCHAR(10))`
      - `sp_forecast_business(IN p_event_id INT UNSIGNED)`
      - `sp_update_pricing_rule(IN p_rule_id INT, IN p_new_value DECIMAL(10,4), IN p_expected_version INT, OUT p_status VARCHAR(20))`
      - `sp_place_order(IN p_order_date DATE, IN p_device_id INT, IN p_region_id INT, IN p_channel_id INT, IN p_items JSON)` (stub in 04_routines.sql kept for schema compatibility; REMOVED from implementation in Stage 3)
      - `sp_recommend_index(IN p_qe_id BIGINT, IN p_table VARCHAR(64), IN p_columns_csv VARCHAR(500), OUT p_rec_id INT)`
      - `sp_apply_index(IN p_rec_id INT)`
      - `sp_reject_index(IN p_rec_id INT)`
      - `sp_add_partition(IN p_table_name VARCHAR(64), IN p_boundary DATE)`
      - `sp_snapshot_sizes()`
      - `sp_forecast_database(IN p_event_id INT UNSIGNED)`
      - `sp_insert_qe(IN p_db_user VARCHAR(64), IN p_digest_text TEXT, IN p_started_at DATETIME(3), IN p_duration_ms DECIMAL(12,3), IN p_rows_examined INT UNSIGNED, IN p_rows_sent INT UNSIGNED, OUT p_qe_id BIGINT UNSIGNED)`
      - `sp_insert_qpn(IN p_qe_id BIGINT, IN p_node_id SMALLINT, IN p_parent_node_id SMALLINT, IN p_operation VARCHAR(200), IN p_table_name VARCHAR(64), IN p_pct_time DECIMAL(6,2), IN p_est_rows BIGINT, IN p_actual_rows BIGINT)`
      - `sp_record_index_test(IN p_rec_id INT, IN p_before_ms DECIMAL(12,3), IN p_after_ms DECIMAL(12,3))`
      - `sp_flag_query(IN p_qe_id BIGINT)`
    - No Oracle PL/SQL syntax anywhere in this file
    - _Requirements: 5.1, 5.2, 5.4, 7.9_

  - [x] 1.5 Write 05_triggers_events.sql — triggers and scheduled events
    - All triggers: `DROP TRIGGER IF EXISTS` + `CREATE TRIGGER`; all events: `DROP EVENT IF EXISTS` + `CREATE EVENT`
    - `BEFORE INSERT` trigger on `FACT_SALES`: NOT APPLICABLE (FACT_SALES unused; no synthetic sales data)
    - `AFTER UPDATE` trigger on `PRICING_RULE`: INSERT into AUDIT_LOG with old/new values as JSON, `db_user = SUBSTRING_INDEX(USER(),'@',1)`, `at = NOW(3)`
    - `AFTER DELETE` trigger on `PRICING_RULE`: INSERT into AUDIT_LOG with old value as JSON
    - Do NOT include `SET GLOBAL event_scheduler` — that is a HUMAN STEP (Stage 0); add a comment explaining this
    - EVENT `ev_snapshot_sizes`: `CREATE EVENT ... ON SCHEDULE EVERY 1 DAY STARTS CURRENT_TIMESTAMP DO CALL sp_snapshot_sizes();`
    - EVENT `ev_recalc_forecasts`: nightly; stub body loops through BUSINESS_EVENT calling `sp_forecast_database`
    - _Requirements: 3.1, 5.3, 5.6, 5.7, 12.4, 17.2_

  - [x] 1.6 Write 06_seed_reference.sql — all reference data
    - Use `INSERT IGNORE` or `INSERT ... ON DUPLICATE KEY UPDATE` for idempotency throughout
    - `COMPANY_SEGMENT`: Productivity and Business Processes (PBP), Intelligent Cloud (IC), More Personal Computing (MPC). No Gaming row. (For upgrades from old schema, run 09_fix_segments.sql first.)
    - `DIM_DATE`: cover 2022-07-01 through 2027-06-30; set `fiscal_year`/`fiscal_quarter` (Microsoft FY ends June 30); set `is_black_friday` (last Friday of November each year); set `is_holiday`
    - `DIM_DEVICE`: all five platforms (Xbox, Windows PC, Mobile, Cloud, Mac); `oem` non-null only for Windows PC rows
    - `DIM_REGION`: Americas, EMEA, APAC
    - `DIM_CHANNEL`: Digital, Physical, Subscription
    - `PRODUCT`: at least 100 products total; ~40 Gaming (segment=MPC, product_line='Gaming'), ~31 PBP, ~30 MPC non-Gaming (product_line=NULL); Gaming products: unit_cost_ratio is synthetic-only, cost_amount=NULL in FACT_SALES
    - `USER_SEGMENT_MAP`: `analyst_demo` → MPC segment, product_line='Gaming'; `analyst_all` → all 3 segments, product_line=NULL
    - `CONFIG`: seed DB_ALLOCATED_MB='10240', AT_RISK_FACTOR='1.25', EVENT_PRE_MONTHS='3', EVENT_POST_MONTHS='3', BASELINE_MONTHS='3', MIN_BASELINE_MONTHS='3', FORECAST_LIFT_MONTHS='3', STEAM_TO_REVENUE_SCALE='1.0'
    - _Requirements: 3.7, 2.7_
    - NOTE: FACT_SALES, ORDERS, ORDER_ITEM remain defined but EMPTY (no synthetic data; kept for schema compatibility)

  - [x] 1.7 Write 07_grants.sql — ALL GRANT statements (only file with GRANTs)
    - GRANT to `business_analyst`: SELECT on `v_vm_workload`, `v_financials`, `v_events`, `v_forecast_business`, `v_my_queries`; EXECUTE on `sp_margin_decomposition`, `sp_forecast_business`, `sp_update_pricing_rule`, `sp_place_order` (stub granted in Stage 1; REMOVED from Stage 3); SHOW VIEW on all `v_*` views
    - GRANT to `db_engineer`: SELECT on `QUERY_EXECUTION`, `QUERY_PLAN_NODE`, `INDEX_RECOMMENDATION`, `DB_SIZE_SNAPSHOT`, `CONFLICT_LOG`, `v_events_min`, `v_forecast_database`, `v_partitions`, `v_vm_workload`; SELECT on `performance_schema`; EXECUTE on `sp_apply_index`, `sp_reject_index`, `sp_add_partition`, `sp_snapshot_sizes`, `sp_forecast_database`; SHOW VIEW on all `v_*` views
    - Note: `information_schema` is accessible to all authenticated MySQL users by default; no explicit GRANT is needed for db_engineer to query information_schema.
    - GRANT to `role_telemetry`: INSERT+UPDATE on `QUERY_EXECUTION`, `QUERY_PLAN_NODE`, `INDEX_RECOMMENDATION`; EXECUTE on `sp_recommend_index`, `sp_insert_qe`, `sp_insert_qpn`, `sp_record_index_test`, `sp_flag_query`
    - GRANT roles to users: `GRANT business_analyst TO analyst_demo, analyst_all`; `GRANT db_engineer TO engineer_demo`; `GRANT role_telemetry TO svc_telemetry`
    - `SET DEFAULT ROLE ALL TO analyst_demo, analyst_all, engineer_demo, svc_telemetry`
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6, 2.8, 2.9, 3.9_

  - [x] 1.8 Write run_migrations.sh
    - Execute `01_ddl.sql` through `07_grants.sql` in order
    - Use `MYSQL_PWD` env var instead of `-p` flag; use envsubst with explicit variable list:
      ```bash
      export MYSQL_PWD="$ADMIN_PASSWORD"
      envsubst '${ADMIN_PASSWORD} ${DEMO_ANALYST_PASSWORD} ${DEMO_ANALYST_ALL_PASSWORD} ${DEMO_ENGINEER_PASSWORD} ${SVC_TELEMETRY_PASSWORD}' < "$file" \
        | mysql -h "$DB_HOST" -P "$DB_PORT" -u "$ADMIN_USER" "$DB_NAME" \
        || { echo "ERROR: $file failed. Halting migration." >&2; exit 1; }
      ```
    - `set -euo pipefail`
    - _Requirements: 3.1, 3.6_

  - [x] 1.9 Write tests/run_role_tests.sh
    - Sources `.env`; no running backend required
    - `assert_denied()` function: runs query as target user; asserts "ERROR 1142" appears in stderr; prints PASS or FAIL
    - `assert_row_count()` function: runs query; counts rows; asserts count equals expected value; prints PASS or FAIL
    - Uses `$ANALYST_DEMO_USERNAME`, `$ANALYST_ALL_USERNAME`, `$ENGINEER_DEMO_USERNAME` from `.env`
    - Assertions:
      - `analyst_demo` denied `SELECT 1 FROM QUERY_PLAN_NODE LIMIT 1` (error 1142)
      - `analyst_demo` denied `SELECT 1 FROM INDEX_RECOMMENDATION LIMIT 1` (error 1142)
      - `analyst_demo` denied `SELECT 1 FROM FACT_SALES LIMIT 1` (error 1142)
      - `analyst_demo` denied `SELECT 1 FROM AZURE_VM LIMIT 1` (error 1142)
      - `engineer_demo` denied `SELECT 1 FROM FACT_SALES LIMIT 1` (error 1142)
      - `engineer_demo` denied `SELECT 1 FROM ORDERS LIMIT 1` (error 1142)
      - `engineer_demo` denied `SELECT 1 FROM QUARTERLY_FINANCIALS LIMIT 1` (error 1142)
      - `engineer_demo` denied `SELECT 1 FROM AZURE_VM LIMIT 1` (error 1142)
      - `analyst_demo`'s `COUNT(*)` on `v_vm_workload` equals the admin's `COUNT(*)` on `AZURE_VM`
    - _Requirements: 2.10, 18.1_

  - [x] 1.10 Verify MySQL behaviours and write Stage 1 report
    - Run each verification from the MySQL Behaviour Verification table on the target instance:
      1. EXPLAIN ANALYZE format: run `EXPLAIN ANALYZE SELECT 1`; record actual indentation pattern and `actual time=N..M rows=R loops=L` format
      2. Invisible indexes: create test table; add INVISIBLE index; verify EXPLAIN skips it; set `use_invisible_indexes=on`; verify EXPLAIN uses it; drop test table; record whether VISIBLE/INVISIBLE status appears in `information_schema.STATISTICS`
      3. REORGANIZE PARTITION: create test table with MAXVALUE; REORGANIZE into two partitions; verify in `information_schema.PARTITIONS`
      4. SHOW VIEW for EXPLAIN on views: GRANT SHOW VIEW to a test role; run EXPLAIN on a view as that role; verify no ERROR 1142
      5. `information_schema_stats_expiry`: defer to Stage 3 gate (when `sp_snapshot_sizes` is implemented)
      6. Verify `CREATE TABLE IF NOT EXISTS` is idempotent: run `01_ddl.sql` twice on the schema; assert no errors on second run
      7. Verify `CURRENT_ROLE()` output format: run `SELECT CURRENT_ROLE()` as `analyst_demo`; record whether it returns a backtick-wrapped name, plain name, or the literal `'NONE'`
      8. Verify sp_insert_qe stub is callable via the ODBC Layer: connect as `svc_telemetry` and call `{CALL sp_insert_qe(?,?,?,?,?,?,@p_qe_id)}` with dummy values; verify `@p_qe_id` is returned
    - Run `sql/run_migrations.sh`; verify all 7 files apply without error
    - Run `pytest tests/test_odbc.py::test_odbc_connection tests/test_odbc.py::test_parameterized_query`
    - Run `tests/run_role_tests.sh`; verify all assertions pass
    - Verify `AZURE_VM` partitions in `information_schema.PARTITIONS` (partition check uses AZURE_VM)
    - Verify all GRANT statements appear only in `07_grants.sql`
    - Write `docs/stage_1_report.md`: run commands, test output, MySQL behaviour verification results, open issues
    - **git commit**: `git add -A && git commit -m "Stage 1: schema, roles, views, routines stubs, grants"`
    - _Requirements: 22.1, 22.2, 22.3_

- [ ] 2. Stage 2 — ETL Pipeline

  - [x] 2.1 Write fetch_edgar.py
    - **DONE**: Tag priority RevenueFromContractWithCustomerExcludingAssessedTax, CostOfGoodsAndServicesSold fallback, OperatingIncomeLoss. All 3 anchors validated: FY24Q2=$62,020M, FY25Q4=$76,441M, FY26Q2=$81,273M. FY2022–FY2026 coverage table complete; quarters-vs-annual per year diff=0 across all metrics.
    - _Requirements: 4.1, 4.10, 21.1_

  - [x] 2.2 Write load_financials.py
    - **DONE**: QUARTERLY_FINANCIALS validated against EDGAR facts; 16 passed, 0 warnings (0.000% delta). 48 rows verified.
    - _Requirements: 4.1, 4.7, 21.1, 21.2_

  - [x] 2.3 HUMAN STEP — provide segments.csv (Phase A complete; loader verified)
    - **HUMAN STEP**: Provide `data/raw/segments.csv` with columns: `fiscal_year`, `fiscal_quarter`, `segment`, `revenue (in million dollars)`, `operating_income` (where disclosed), `source_url`, `Months`; no fabricated data
    - **Phase A validation result**: ✅ PASSED — 16 rows (FY23Q1–FY26Q4), no duplicates, no gaps, all revenue numeric/positive, all operating_income blank→NULL, all source_urls present, Months present. Cosmetic anomaly: FY24Q3 "mar" lower-cased.
    - **Unit convention (requires owner approval)**: Values in millions USD. Stored in GAMING_REVENUE_SERIES (separate table, not QUARTERLY_FINANCIALS). EDGAR raw USD ÷ 1,000,000 at load time.
    - **Months column**: Preserved as `months_label VARCHAR(40)` in GAMING_REVENUE_SERIES.
    - **Loader written**: `etl/load_segments_gaming.py` now exists; maps `revenue (in million dollars)` → `revenue_m`, preserves `Months` as `months_label`, leaves `operating_income` NULL, requires `source_url`. Added to `load_sales.sh` as Step 3b.
    - _Requirements: 4.7, 4.12, 22.5_

  - [x] 2.4 HUMAN STEP — provide steam_players.csv (Phase A complete, all phases completed)
    - **HUMAN STEP**: Provide `data/raw/steam_players.csv` with columns: `app_id`, `obs_date`, `avg_players`, `peak_players`; no fabricated data
    - **Phase A validation result**: ✅ PASSED — 5 app IDs. Coverage: apps 1938090/1172620/1240440/1551360 have 48 rows each (Oct 2022 – Sep 2026); app 1716740 (Starfield) has 37 rows (Sep 2023 – Sep 2026). Total: 4×48 + 37 = 229 rows. No duplicates, no missing months within each app's range, all obs_date = 1st of month, avg_players is decimal, peak_players is integer, no negatives.
    - App IDs verified: 1938090=Call of Duty, 1172620=Sea of Thieves, 1240440=Halo Infinite, 1551360=Forza Horizon 5, 1716740=Starfield.
    - Task state set to [~] — data file provided, loader to be updated for DECIMAL avg_players after Phase B approval.
    - _Requirements: 4.2, 4.12, 22.5_

  - [x] 2.5 Write load_steam.py — STEAM_PLAYER only
    - Load `steam_players.csv` into `STEAM_PLAYER` only; do NOT compute EVENT_WINDOW_MONTH rows (that is `compute_event_windows.py`)
    - **CORRECTED validation (Phase B)**: `obs_date` parses as ISO 8601 AND must be the 1st of the month; `avg_players` is a non-negative **decimal** (DECIMAL(12,2)), NOT integer; `peak_players` is a non-negative integer
    - Per-row rejection with log entry; `INSERT IGNORE` for idempotency
    - _Requirements: 4.2, 21.1, 21.2_

  - [x] 2.6 Write load_trends.py
    - pytrends for keywords: "Call of Duty", "Game Pass", "Xbox price"
    - On `TooManyRequestsError`: fall back to `data/raw/trends_manual.csv`; log rate-limit event with UTC timestamp
    - If neither source is available: log error and exit 1
    - Write CSV results to `data/raw/`
    - _Requirements: 4.3, 4.10_

  - [x] 2.7 HUMAN STEP — provide events.csv (done)
    - **HUMAN STEP**: Provide `data/raw/events.csv` with columns: `event_type`, `name`, `start_date`, `end_date`, `expected_lift`, `steam_app_id` (INT or blank), `source_url`; `steam_app_id` is required for game_launch events that have a Steam title; leave blank/NULL for non-Steam events (Black Friday, price changes, etc.); must include at least one `game_launch` event with `start_date` after today; no fabricated data
    - Agent must stop and wait for operator confirmation before proceeding
    - _Requirements: 4.5, 4.12, 22.5_

  - [x] 2.8 Write load_events.py
    - Validate each row: `source_url` non-null and non-empty; `end_date >= start_date`; `event_type` in allowed ENUM values; `steam_app_id` INT or NULL (blank → NULL); per-row rejection (not batch abort) with log identifier/field/expected/actual
    - Future-event check: verify at least one `game_launch` with `start_date > CURDATE()` at load time; if none, log warning and document as_of_date workaround; do not abort
    - `INSERT IGNORE` for idempotency
    - _Requirements: 4.4, 4.5, 21.1, 21.2, 21.3_

  - [x] 2.9 Write compute_event_windows.py (renamed to monthly; formerly day-based)
    - Read `STEAM_PLAYER` and `BUSINESS_EVENT` from DB via mysql-connector-python
    - **Read CONFIG keys**: EVENT_PRE_MONTHS, EVENT_POST_MONTHS, BASELINE_MONTHS, MIN_BASELINE_MONTHS from CONFIG table (never hardcode)
    - Process only `game_launch` events **with a non-NULL steam_app_id** (not all event types)
    - For each qualifying event: compute `EVENT_WINDOW_MONTH` rows for month offsets −EVENT_PRE_MONTHS..+EVENT_POST_MONTHS; offset 0 = the calendar month containing `start_date`
    - Baseline = mean avg_players of the BASELINE_MONTHS months immediately before offset 0 (offsets −BASELINE_MONTHS..−1)
    - `lift_ratio = avg_players_for_offset_month / baseline`; NULL if:
      - Fewer than MIN_BASELINE_MONTHS baseline months exist in STEAM_PLAYER for this app_id
      - OR any required baseline month is absent (missing months are never zero)
    - Log missing months to stdout and to `data/clean/lift_warnings_<event_id>.log`
    - `INSERT IGNORE` for idempotency; log count of rows inserted per event
    - _Requirements: 4.2_

  - [x] 2.9b Profile and load Azure VM workload table
    - **Dataset**: `data/raw/azure/vmtable.csv` — 2,695,548 rows, 11 columns, no header, trace-relative seconds
    - **Columns** (in order): vm_id, subscription_id, deployment_id, created_at, deleted_at, max_cpu, avg_cpu, p95_max_cpu, vm_category, vcpu_bucket, memory_bucket
    - **Profile CSV first**: Write `etl/profile_azure_csv.py` to read first 10K rows, infer data types, check ranges, output DDL recommendations
    - **DDL**: Create `AZURE_VM` table in migration `sql/migrations/10a_azure_vm.sql`:
      - PK: (vm_id, trace_day) where trace_day = FLOOR(created_at / 86400)
      - PARTITION BY RANGE on trace_day (in the PK)
      - NO FK constraints
      - Label: "Azure Public Dataset V2 VM trace (real, Microsoft-published workload data)"
      - source_url and licence: TODO placeholders (do not state licence from memory)
    - **Load**: Write `etl/load_azure_vm.py`:
      - LOAD DATA LOCAL INFILE in batches by trace_day range (e.g., days 0-29, 30-59, etc.)
      - After each batch: INSERT INTO DB_SIZE_SNAPSHOT (is_synthetic=FALSE, label='load-progress snapshot')
      - No clamping, no invented dates, preserve all trace-relative times as-is
      - Final verification: row count MUST be exactly 2,695,548
    - **DONE**: Loaded exactly 2,695,548 rows in 37.5s across 6 batches. All DB_SIZE_SNAPSHOT progress entries written. [PASS] row count verified.
    - **Checksum file**: Write `data/raw/azure/CHECKSUM.txt` with SHA256 and row count; keep data/raw/azure/ in .gitignore
    - _Requirements: 4.11, 21.1, 21.2_

  - [REMOVED] 2.10 Write gen_synthetic_sales.py
    - **REMOVED**: Synthetic sales generation not needed with real Azure VM workload data
    - _Requirements: 4.6, 21.1_

  - [x] 2.11 Write load_sales.sh — ETL master orchestrator
    - **UPDATED**: Step 3b `load_segments_gaming.py` added between segments.csv human confirmation and load_steam.py
    - **UPDATED**: All synthetic sales steps (9-14) removed; replaced with real `load_azure_vm.py` (step 9) + row-count reconciliation (step 10)
    - ETL sequence: `fetch_edgar.py` → `load_financials.py` → [HUMAN: confirm segments.csv] → `load_segments_gaming.py` → `load_steam.py` → `load_trends.py` → [HUMAN: confirm events.csv] → `load_events.py` → `compute_event_windows.py` → `load_azure_vm.py`; no synthetic sales generation
    - `set -euo pipefail`; log UTC + source filename on any step failure; halt on failure
    - _Requirements: 4.8, 4.10, 4.11_

  - [x] 2.12 Stage 2 gate — seq-scan verification
    - **HUMAN STEP**: Confirm ETL is complete and AZURE_VM has exactly 2,695,548 rows; confirm no secondary index on AZURE_VM exists - confirmed by human
    - Execute Heavy_Demo_Query via MySQL CLI as the user who has the grant (`engineer_demo` or `analyst_demo`; no backend required at this stage):
      `mysql -u engineer_demo -p$DEMO_ENGINEER_PASSWORD $DB_NAME < tests/heavy_demo_query.sql`
    - Run `EXPLAIN` on the underlying `v_vm_workload` query; output must show full table scan on `AZURE_VM`
    - Query must take > 2 seconds; if < 2s, check query is scanning AZURE_VM without index (query filters/groups on vm_category, vcpu_bucket, created_at)
    - Manually create candidate index on non-primary-key columns (PK is (vm_id, trace_day), so index on those shows no speedup): `CREATE INDEX idx_stage2_test ON AZURE_VM (vm_category, vcpu_bucket, created_at) ALGORITHM=INPLACE;`
    - Rerun the query; confirm >= 50% speedup; record before/after times in `docs/stage_2_report.md`
    - Drop the index: `DROP INDEX idx_stage2_test ON AZURE_VM;`
    - Record Heavy_Demo_Query SQL in `tests/heavy_demo_query.sql`
    - Write `docs/stage_2_report.md`: ETL commands, row counts (2,695,548 expected for AZURE_VM), EXPLAIN output, query duration before/after index
    - Verify `information_schema_stats_expiry=0` SESSION effect; record result in report
    - **git commit**: `git add -A && git commit -m "Stage 2: ETL complete"`
    - **HUMAN STEP — mysqldump**: Prompt operator to run `mkdir -p ~/margin_db_backups && mysqldump -u $ADMIN_USER $DB_NAME > ~/margin_db_backups/margin_db_stage2.sql` before proceeding to Stage 3; dump file written outside repo to avoid large files in git history
    - _Requirements: 4.11, 22.1, 22.2_

- [x] 3. Stage 3 — Stored Routines and DB-Intelligence

  - [x] 3.1 Implement sp_snapshot_sizes and load-progress snapshots
    - Full `sp_snapshot_sizes`: `SET SESSION information_schema_stats_expiry=0`; INSERT INTO `DB_SIZE_SNAPSHOT` from `information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()`; use `ON DUPLICATE KEY UPDATE` on `snapped_at` (UNIQUE KEY prevents duplicates)
    - Load-progress snapshots: INSERT into `DB_SIZE_SNAPSHOT` after each AZURE_VM batch load (already done in task 2.9b); `INSERT IGNORE` for idempotency
    - **DONE**: `sp_snapshot_sizes` implemented in `sql/migrations/11_stage3_routines.sql` and verified.
    - Verify `ev_snapshot_sizes` EVENT is ENABLED in `information_schema.EVENTS`
    - _Requirements: 17.1, 17.2, 17.3_

  - [x] 3.2 Implement sp_margin_decomposition
    - Replace stub with full logic: intersect the requested segment with the caller's USER_SEGMENT_MAP using `WHERE segment_id IN (SELECT segment_id FROM USER_SEGMENT_MAP WHERE db_user=SUBSTRING_INDEX(USER(),'@',1)) AND name = p_segment`; also apply product_line filter if USM.product_line IS NOT NULL; if no matching rows, return empty result
    - Read from QUARTERLY_FINANCIALS; compute Δoperating_income, Δrevenue, Δmargin_rate per segment; respect basis caveat
    - Revenue effect = Δrevenue × period_A_margin_rate; Margin-rate effect = period_B_revenue × Δmargin_rate
    - Compute segment-level decomposition on QUARTERLY_FINANCIALS: operating income change = revenue effect + margin-rate effect
    - Return result set: `(component VARCHAR(30), value_delta DECIMAL(20,4))`
    - Support optional `@group_by` session variable for additional output grouping
    - All references to calling user via `SUBSTRING_INDEX(USER(),'@',1)`; no `CURRENT_USER()`
    - **DONE**: Full 2-component SP applied via `sql/migrations/11_stage3_routines.sql` and verified across analyst personas.
    - _Requirements: 15.1, 5.1, 5.2, 5.10_

  - [x] 3.3 Implement sp_forecast_business
    - Read CONFIG keys at start: EVENT_PRE_MONTHS, EVENT_POST_MONTHS, BASELINE_MONTHS, MIN_BASELINE_MONTHS, FORECAST_LIFT_MONTHS (all default 3); never hardcode these values
    - Baseline: trailing 4-quarter average monthly revenue from QUARTERLY_FINANCIALS (post-2023-10 quarters only, to use post-Activision-acquisition data); verify caller has segment access via USER_SEGMENT_MAP; return empty result set if not permitted
    - Lift source selection (in order of preference):
      a. IF event has non-NULL steam_app_id AND EVENT_WINDOW_MONTH has ≥1 non-NULL lift_ratio at offsets 0..FORECAST_LIFT_MONTHS-1:
           measured_lift = AVG(non-NULL lift_ratio at offsets 0..FORECAST_LIFT_MONTHS-1) − 1
           lift_source = 'measured'
      b. ELSE IF this is a game_launch event with no measured lift (e.g. future title like Modern Warfare 4):
           Look up all prior game_launch events of the same event_type that DO have measured lift
           IF at least 1 prior launch has measured data:
             derived_lift = AVG(measured_lift across those prior launches)
             n_prior_launches = COUNT of those launches
             lift_source = 'derived_from_prior_launches'
             confidence capped at 'medium' (regardless of other inputs)
           ELSE fall through to (c)
      c. ELSE IF BUSINESS_EVENT.expected_lift IS NOT NULL:
           event_lift = expected_lift − 1; lift_source = 'expected_lift'
      d. ELSE: event_lift = 0; lift_source = 'zero_fallback'
    - Write FORECAST_LINE: metric_name='lift_source', metric_value=NULL, error_indicator=lift_source (stores which path was taken)
    - When lift_source='derived_from_prior_launches': write additional FORECAST_LINE with metric_name='n_prior_launches', metric_value=n_prior_launches
    - Add CONFIG key: STEAM_TO_REVENUE_SCALE (documents the proxy scale factor; default '1.0'; read but not yet used in formula — document as illustrative proxy in FORECAST_LINE)
    - UI displays as SCENARIO with: assumption source (measured/derived/expected), lift value, lift_source label, Steam game if applicable
    - `projected_revenue_impact = baseline_monthly_revenue × event_duration_months × event_lift`
    - Confidence tier: high (all 3 inputs, lift_source='measured'), medium (2 inputs OR lift_source='derived_from_prior_launches'), low (0–1 inputs or zero_fallback)
    - `as_of_date` from `@as_of_date` session variable (NULL → CURDATE())
    - Write to FORECAST + FORECAST_LINE; return result set
    - `SUBSTRING_INDEX(USER(),'@',1)` throughout; no `CURRENT_USER()`
    - No Gaming cost, operating income or margin anywhere in this procedure
    - **DONE**: Implemented in `sql/migrations/12_stage3_remaining.sql` and verified for measured and derived paths.
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.7, 5.10_

  - [x] 3.4 Implement sp_update_pricing_rule
    - Replace stub: `UPDATE PRICING_RULE SET price_value=p_new_value, version=version+1 WHERE rule_id=p_rule_id AND version=p_expected_version`
    - `ROW_COUNT()=1` → `p_status='OK'`; version already incremented
    - `ROW_COUNT()=0` → INSERT CONFLICT_LOG, `p_status='CONFLICT'`; no SIGNAL on mismatch
    - `SUBSTRING_INDEX(USER(),'@',1)` for `attempted_by` in CONFLICT_LOG
    - **DONE**: Implemented in `sql/migrations/11_stage3_routines.sql` and validated via property testing.
    - _Requirements: 12.1, 12.2_

  - [REMOVED] 3.5 Implement sp_place_order
    - REMOVED / NOT APPLICABLE: Order placement and synthetic transaction tables (ORDERS, ORDER_ITEM, FACT_SALES) are unused with real Azure VM workload data
    - _Requirements: 13.1_

  - [x] 3.6 Implement sp_recommend_index and sp_record_index_test
    - `sp_recommend_index(IN p_qe_id BIGINT, IN p_table VARCHAR(64), IN p_columns_csv VARCHAR(500), OUT p_rec_id INT)`:
      1. Deduplicate: check existing active rec for same `(table_name, columns_csv)` with status in ('proposed','testing','applied'); if found, RETURN (no-op)
      2. Whitelist check: `p_table` must be in allowed list (AZURE_VM); `SIGNAL SQLSTATE '45001'` if not
      3. Column validation: each column in `p_columns_csv` must match `^\w+$` AND exist in `information_schema.COLUMNS` for `p_table`; `SIGNAL SQLSTATE '45002'` if invalid
      4. `INSERT INTO INDEX_RECOMMENDATION (..., index_name, status) VALUES (..., 'placeholder', 'testing')`
      5. `SET v_rec_id = LAST_INSERT_ID(); SET p_rec_id = v_rec_id`
      6. `UPDATE INDEX_RECOMMENDATION SET index_name = CONCAT('idx_rec_', v_rec_id) WHERE rec_id = v_rec_id`
      7. Dynamic `ALTER TABLE {p_table} ADD INDEX CONCAT('idx_rec_', v_rec_id) ({p_columns_csv}) INVISIBLE, ALGORITHM=INPLACE` via PREPARE/EXECUTE/DEALLOCATE
         (INVISIBLE before the comma; index is not used by optimizer until engineer approves)
    - `sp_record_index_test(p_rec_id, p_before_ms, p_after_ms)`: `UPDATE INDEX_RECOMMENDATION SET before_ms=p_before_ms, after_ms=p_after_ms WHERE rec_id=p_rec_id`
    - Also implement `sp_flag_query(IN p_qe_id BIGINT)`: `UPDATE QUERY_EXECUTION SET flagged=1 WHERE qe_id=p_qe_id;` (SQL SECURITY DEFINER)
    - **DONE**: Implemented in migrations 11 & 12 and verified.
    - _Requirements: 7.5, 7.6, 7.9_

  - [x] 3.7 Implement sp_apply_index and sp_reject_index
    - `sp_apply_index(p_rec_id)`:
      - Before calling ALTER INDEX VISIBLE: verify `after_ms IS NOT NULL`; if NULL, return error result set `SELECT 'Index test not yet complete' AS result`; the engineer router maps this to HTTP 409
      - Verify `status='testing'`; dynamic `ALTER TABLE ... ALTER INDEX ... VISIBLE`; UPDATE status='applied', decided_by, decided_at=NOW(3); return `SELECT 'applied' AS result`
    - `sp_reject_index(p_rec_id)`: verify `status IN ('testing','proposed')`; dynamic `ALTER TABLE ... DROP INDEX`; if ALTER fails (index not existing), return error result set without re-raising; UPDATE status='rejected'; return `SELECT 'rejected' AS result`
    - **DONE**: Implemented in migration 12 and verified.
    - _Requirements: 7.7, 7.8, 7.10_

  - [x] 3.8 Implement sp_add_partition
    - Dynamic SQL: `REORGANIZE PARTITION p_catch INTO (new named partition + new p_catch MAXVALUE)`
    - Whitelist `p_table_name` (only AZURE_VM allowed); SIGNAL if not
    - Generate partition name from boundary date in `p_FYyyQq` format
    - PREPARE/EXECUTE/DEALLOCATE PREPARE pattern
    - **DONE**: Implemented in migration 12 and verified.
    - _Requirements: 14.2_

  - [x] 3.9 Implement sp_forecast_database
    - Replace stub with linear regression on DB_SIZE_SNAPSHOT (last 90 days)
    - Read `DB_ALLOCATED_MB` from `CONFIG WHERE config_key='DB_ALLOCATED_MB'`; fall back to `@db_allocated_mb` session variable if CONFIG row absent
    - Read `AT_RISK_FACTOR` from `CONFIG WHERE config_key='AT_RISK_FACTOR'`; default 1.25 if absent
    - Guard: `IF slope_mb_per_day <= 0` → set `days_to_90=NULL`; write FORECAST_LINE with `error_indicator='slope_non_positive'`; RETURN without exception
    - Compute projected_rows using: `projected_rows = current_rows + rows_per_day × event_duration_days × (1 + event_lift)`; event_lift sourced from EVENT_WINDOW_MONTH (monthly, CONFIG-driven) — same lookup as sp_forecast_business
    - Compute growth: `projected_growth_mb = slope_mb_per_day × (1 + event_lift) × event_duration_days`
    - At-risk queries: `projected_duration_ms = baseline_ms × (projected_rows / current_rows)`; mark at_risk if `>= AT_RISK_FACTOR × baseline_ms`; ordered by event date
    - 30-day warning: write FORECAST_LINE `metric_name='warning_30_day'` if `days_to_90 < 30`
    - `as_of_date` from session variable
    - **DONE**: Implemented in migration 12 and verified.
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9, 17.4, 17.5_

  - [x] 3.10 Implement sp_insert_qe and sp_insert_qpn
    - `sp_insert_qe`: `INSERT INTO QUERY_EXECUTION(...) VALUES(...); SET p_qe_id = LAST_INSERT_ID();` SQL SECURITY DEFINER
    - `sp_insert_qpn`: `INSERT INTO QUERY_PLAN_NODE(...) VALUES(...);` SQL SECURITY DEFINER
    - Both already granted to `role_telemetry` via `07_grants.sql` (task 1.7)
    - **DONE**: Implemented in migration 11 and verified.
    - _Requirements: 7.3_

  - [x] 3.11 Write property-based test for optimistic lock (Property 1)
    - Use `hypothesis`; generate concurrent updates with same `expected_version`
    - Assert exactly one update returns HTTP 200 (status='OK'), all others return HTTP 409 (status='CONFLICT')
    - Assert `version = initial_version + 1` after N attempts; assert `CONFLICT_LOG` has exactly N−1 rows
    - **Property 1: Optimistic Lock Exclusivity**
    - **Validates: Requirements 12.1, 12.2, 12.3**
    - **DONE**: `tests/test_optimistic_lock_property.py` passed with Hypothesis.

  - [x] 3.12 Write property-based test for pct_time invariant (Property 2)
    - Use `hypothesis`; generate arbitrary `actual_time` (M, per-loop end time) and `loops` values for a set of plan nodes
    - Assert `pct_time = (actual_ms × loops) / root_total_ms × 100` where `root_total_ms = root_actual_ms × root_loops`, within 0.01 tolerance
    - Assert root node `pct_time = 100.0`
    - Assert `duration_ms = root_actual_ms` (M from root node, not root_total_ms)
    - **Property 2: pct_time Sum Invariant**
    - **Validates: Requirements 7.1**
    - **DONE**: `tests/test_pct_time_property.py` passed with Hypothesis.

  - [x] 3.13 Write property-based test for SQL console multi-statement rejection (Property 6)
    - Grammar-based strategy: generate valid `SELECT ...` and `WITH ... SELECT ...` strings (expect accept=True); generate multi-statement strings (semicolon + non-whitespace) and DML strings (expect accept=False)
    - Assert validation function returns True for all valid inputs, False for all invalid inputs
    - **Property 6: SQL Console Multi-Statement Rejection**
    - **Validates: Requirements 16.1**
    - **DONE**: `tests/test_sql_console_rejection_property.py` and `backend/sql_validator.py` passed with Hypothesis.

  - [x] 3.14 Stage 3 gate
    - Re-run `tests/run_role_tests.sh`; verify no regressions
    - Call `sp_snapshot_sizes()` via MySQL CLI; verify row appears in `DB_SIZE_SNAPSHOT`
    - Verify `information_schema_stats_expiry=0` SESSION effect: run INSERT, re-query `information_schema.TABLES`, verify `data_length` updates; record in `docs/stage_3_report.md`
    - Call `sp_margin_decomposition` with known segment/period values; verify 2-component decomposition result set (revenue effect, margin-rate effect)
    - Verify AUDIT_LOG trigger fires on `PRICING_RULE` UPDATE (check row count before/after)
    - Verify both EVENTs (`ev_snapshot_sizes`, `ev_recalc_forecasts`) appear as ENABLED in `information_schema.EVENTS`
    - Verify EXPLAIN ANALYZE on `AZURE_VM` still shows full table scan (no index added yet)
    - Write `docs/stage_3_report.md`
    - **git commit**: `git add <paths> && git commit -m "Stage 3: stored routines, telemetry SPs"`
    - **DONE**: `tests/run_stage3_gate.py` passed all checks, documented in `docs/stage_3_report.md`.
    - _Requirements: 22.1, 22.2_

- [ ] 4. Stage 4 — Backend API

  - [ ] 4.1 Implement main.py
    - App factory; startup env-var check (all required variables from `.env.example`); raise RuntimeError if any missing
    - CORS: `allow_origins=["http://localhost:5173"]`
    - Register analyst and engineer routers
    - Mount `/docs` (FastAPI automatic)
    - Startup check: verify not running as root (`os.getuid() != 0` on Unix)
    - _Requirements: 6.1, 6.4, 6.7, 6.8, 6.9_

  - [ ] 4.2 Implement auth.py — JWT authentication with pyjwt
    - `POST /login`: open pyodbc connection as submitted user; `SELECT CURRENT_ROLE()`; if result is None, empty string, or the literal `'NONE'`: fall back to `SHOW GRANTS` to extract role
    - Handle backtick-wrapped role names: strip backticks from role name before comparing to `'business_analyst'`, `'db_engineer'`, etc. (e.g., `` `business_analyst` `` → `business_analyst`)
    - `import jwt` (pyjwt); `jwt.encode({"sub": username, "role": role, "exp": now+3600}, JWT_SECRET, algorithm="HS256")`
    - `jwt.decode(token, JWT_SECRET, algorithms=["HS256"])` — raises `jwt.ExpiredSignatureError` or `jwt.InvalidTokenError` on failure
    - Store `(username, password)` in `credential_map[token]`; never write to disk or logs
    - Return HTTP 401 on connect failure; HTTP 401 on expired/missing JWT; HTTP 403 on role mismatch
    - _Requirements: 1.1, 1.2, 1.3, 1.6, 1.7, 1.9_

  - [ ] 4.3 Implement db.py — ODBC helpers
    - `get_connection(username, password, role)`: `DRIVER={MySQL ODBC 8.0 Unicode Driver}; charset=utf8mb4`; `timeout=10`; set `MAX_EXECUTION_TIME=30000` immediately after opening if role is `business_analyst`
    - `call_sp(conn, sp_name, params)`: `{CALL sp(?,?,?)}`; check `cursor.description is not None` before `fetchall()`; after `fetchall()`, drain any trailing result sets with `while cursor.nextset(): pass`; call `conn.commit()` after every SP call
    - `call_sp_out(conn, sp_name, in_params, out_count)`: CALL statement format `{CALL sp_name(?, ?, @out0)}` — bind in-params with `?`, reference out-params as `@out0` etc.; after the CALL, drain intermediate result sets with `while cursor.nextset(): pass` loop; then `SELECT @out0, ...`; call `conn.commit()`
    - On connection failure: raise domain exception; router converts to HTTP 503
    - _Requirements: 6.1, 1.4_

  - [ ] 4.4 Implement telemetry.py — EXPLAIN ANALYZE, plan parser, background task
    - `parse_explain_tree(explain_text)`:
      - Indent stack for parent assignment: `depth = leading_spaces / 2`; pop stack until top depth < current depth; new node's parent = stack top; push current node
      - Parse `actual time=N..M loops=L`: `actual_ms = M` (per-loop end time); `total_ms = M × loops`
      - `pct_time = node.total_ms / root.total_ms × 100`
      - `duration_ms` (for QUERY_EXECUTION) = root `actual_ms` (M from root node, not root total_ms)
      - `operation` = text before first `  (`; `table_name` from `Table scan on <tbl>` (AZURE_VM has no alias)
      - `rows_examined` = SUM(`actual_rows × loops`) across all "Table scan on" nodes
    - `write_telemetry(conn_analyst, conn_telemetry, sql_text, started_at)`:
      1. Run `EXPLAIN ANALYZE {sql_text}` on conn_analyst; parse plan
      2. Run plain sql_text on conn_analyst; `result_rows = cursor.fetchall()`; `rows_sent = len(result_rows)`
      3. Write QUERY_EXECUTION via `call_sp_out(conn_telemetry, "sp_insert_qe", [...], 1)` → `qe_id`
      4. Write QUERY_PLAN_NODE rows via `call_sp(conn_telemetry, "sp_insert_qpn", [...])` per node
      5. Flag check: any node with `operation.startswith("Table scan on") AND pct_time > 50`?
      6. If flagged: call `call_sp(conn_telemetry, "sp_flag_query", [qe_id])`; return `(result_rows, qe_id, True, background_info)`
      7. Else: return `(result_rows, qe_id, False, None)`
    - `run_index_recommendation(conn_telemetry, qe_id, table, columns_csv, sql_text, username, password)`:
      - Open fresh analyst connection for before/after timing
      - Measure `before_ms`: median of 3 runs of `sql_text` WITHOUT `use_invisible_indexes` (baseline)
      - Call `sp_recommend_index(qe_id, table, columns_csv, @out0)` via `call_sp_out(conn_telemetry, ...)` → `p_rec_id`
      - Measure `after_ms`: median of 3 runs with `SET SESSION optimizer_switch='use_invisible_indexes=on'`
      - Call `sp_record_index_test(rec_id, before_ms, after_ms)` via `conn_telemetry`
    - `/sql` endpoint: validate SQL; run `write_telemetry`; if flagged enqueue `BackgroundTasks.add_task(run_index_recommendation, ...)`; return immediately with `{qe_id, flagged, rows, duration_ms}`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.6, 7.7, 16.2_

  - [ ] 4.5 Implement analyst router
    - All endpoints require `business_analyst` role; return HTTP 403 if mismatch
    - `GET /events`: `SELECT * FROM v_events`; return event list
    - `GET /margin?segment&period_a&period_b&group_by`: SET `@group_by = ?` before calling SP if group_by provided; segment must be one of PBP/IC/MPC; `call_sp(conn, "sp_margin_decomposition", [segment, period_a, period_b])`
    - `GET /forecast/business/{event_id}?as_of_date=`: SET `@as_of_date`; `call_sp(conn, "sp_forecast_business", [event_id])`
    - `POST /sql`: validate single SELECT or `WITH...SELECT`; reject `re.search(r';\s*\S', sql)`; reject DML at word boundary; call `write_telemetry`; if flagged enqueue background task; return `{qe_id, flagged, rows, duration_ms}` immediately; ~60s timeout via `MAX_EXECUTION_TIME=30000`
    - `PUT /pricing/{rule_id}`: `call_sp_out` with `sp_update_pricing_rule`; p_status='OK' → HTTP 200; 'CONFLICT' → HTTP 409
    - `GET /my-queries`: `SELECT * FROM v_my_queries`
    - `POST /analyst/orders`: REMOVED / NOT APPLICABLE (order placement unused)
    - _Requirements: 6.2, 6.5, 6.6, 1.4, 1.8, 12.3, 13.2, 15.2, 16.1_

  - [ ] 4.6 Implement engineer router
    - All endpoints require `db_engineer` role; return HTTP 403 if mismatch
    - `GET /workload`: query `performance_schema.events_statements_summary_by_digest` top 20 by count_star
    - `GET /slow-queries`: `QUERY_EXECUTION WHERE flagged=1 ORDER BY started_at DESC`
    - `GET /slow-queries/{qe_id}/plan`: `QUERY_PLAN_NODE WHERE qe_id=? ORDER BY node_id`
    - `GET /index-recs`: `INDEX_RECOMMENDATION JOIN QUERY_EXECUTION WHERE status IN ('proposed','testing')`
    - `POST /index-recs/{id}/approve`: `call_sp(conn, "sp_apply_index", [id])`; if result is 'Index test not yet complete' return HTTP 409; otherwise return 200
    - `POST /index-recs/{id}/reject`: `call_sp(conn, "sp_reject_index", [id])`
    - `GET /partitions`: `SELECT * FROM v_partitions`
    - `POST /partitions`: `call_sp(conn, "sp_add_partition", [table_name, boundary])`
    - `GET /capacity/forecast`: read FORECAST (kind='database') + FORECAST_LINE
    - `POST /forecast/database/{event_id}?as_of_date=`: SET `@as_of_date`; `call_sp(conn, "sp_forecast_database", [event_id])`
    - `GET /conflicts`: `SELECT * FROM CONFLICT_LOG ORDER BY at DESC LIMIT 100`
    - `GET /engineer/events`: `SELECT * FROM v_events_min`
    - _Requirements: 6.3, 7.10, 9.1, 9.7, 14.3, 14.4, 17.5, 17.6_

  - [ ] 4.7 Implement Pydantic request and response models
    - `models/requests.py`: `LoginRequest`, `SqlRequest (sql: str)`, `PricingUpdateRequest (new_value, expected_version)`, `OrderRequest (order_date, device_id, region_id, channel_id, items: list)`, `PartitionRequest (table_name, boundary)`
    - `models/responses.py`: `ForecastBusinessResponse`, `ForecastDatabaseResponse`, `MarginDecompositionResponse`, `SlowQueryResponse`, `IndexRecResponse`, `ConflictLogResponse`, `CapacityForecastResponse`, `SqlResponse (qe_id, flagged, rows, duration_ms)`
    - No internal connection details exposed in any response model
    - _Requirements: 6.5_

  - [ ] 4.8 Write test_concurrency.py
    - Setup: INSERT test PRICING_RULE row via admin connection (table starts EMPTY) via admin connection before spawning threads
    - Spawn two threads that simultaneously submit `PUT /pricing/{test_rule_id}` with that version as `expected_version`
    - Assert `sorted(results) == [200, 409]`
    - Assert exactly one `CONFLICT_LOG` row for `rule_id=1` with `stale_version=current_version` created during this test
    - Teardown: DELETE test PRICING_RULE row and CONFLICT_LOG entries via admin connection (via admin connection)
    - _Requirements: 18.2_

  - [ ] 4.9 Write property-based test for full-scan flag rule completeness (Property 3)
    - Use `hypothesis`; generate arbitrary sets of QUERY_PLAN_NODE-like dicts with random `operation` and `pct_time` values
    - Assert `flagged=True` if and only if at least one node has `operation.startswith("Table scan on") AND pct_time > 50`
    - Verify no false positives and no false negatives
    - **Property 3: Full-Scan Flag Rule Completeness**
    - **Validates: Requirements 7.4, 11.2**

  - [ ] 4.10 Write property-based test for projected query duration formula (Property 5)
    - Use `hypothesis`; generate arbitrary positive `baseline_ms`, `current_rows`, `projected_rows`
    - Assert `projected_duration_ms == baseline_ms × (projected_rows / current_rows)` within floating-point tolerance
    - Assert `at_risk = True` iff `projected_duration_ms >= AT_RISK_FACTOR × baseline_ms` where AT_RISK_FACTOR defaults to 1.25
    - **Property 5: Projected Duration Formula Correctness**
    - **Validates: Requirements 9.3, 9.4**

  - [ ] 4.11 Write test_demo_loop.py
    - Drop any prior test index at start via admin connection for idempotency (`ALTER TABLE AZURE_VM DROP INDEX IF EXISTS idx_rec_...`)
    - Step 1: `POST /sql` with Heavy_Demo_Query (scans AZURE_VM via v_vm_workload); assert `qe_id` in response; record pre-index `duration_ms` from API response (not wall-clock)
    - Step 2: Poll `GET /index-recs` up to 60 seconds (2-second intervals) until a recommendation for that `qe_id` appears; assert `rec_id` found
    - Step 3: `POST /index-recs/{rec_id}/approve`; assert HTTP 200
    - Step 4: Poll via admin connection `SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=? AND TABLE_NAME=? AND INDEX_NAME=?` (1-second intervals) up to 30 seconds until count > 0; assert `index_visible=True`
    - Step 5: Rerun Heavy_Demo_Query 2× via `POST /sql`; collect `duration_ms` from API responses; assert each post-index `duration_ms` is ≤ 0.5 × the pre-index `duration_ms` from step 1 (comparing `duration_ms` values from API responses, not wall-clock timing)
    - Step 6: Call `POST /forecast/database/{event_id}` with a known future game_launch `event_id`; wait for HTTP 200 response
    - Step 7: `GET /capacity/forecast`; assert `days_to_90pct` is not None; assert at least one query with `projected_ms >= AT_RISK_FACTOR × baseline_ms`
    - Assert total elapsed time <= 120 seconds (all steps including polling must complete within 120 seconds total)
    - _Requirements: 11.6, 18.3_

  - [ ] 4.12 Stage 4 gate
    - Run `pytest tests/test_concurrency.py tests/test_demo_loop.py tests/test_odbc.py`; all must pass
    - Verify `GET /docs` returns HTTP 200
    - Verify `GET /slow-queries` with analyst JWT returns HTTP 403
    - Verify `POST /sql` with multi-statement input returns HTTP 422 without opening a DB connection
    - Write `docs/stage_4_report.md`
    - **git commit**: `git add -A && git commit -m "Stage 4: backend API complete"`
    - _Requirements: 22.1, 22.2_

- [ ] 5. Stage 5 — Frontend

  - [ ] 5.1 Implement API client and auth hooks
    - `client.js`: axios instance with `VITE_API_BASE_URL`; JWT stored in module-level `_token` (not localStorage); `setToken`/`clearToken` exports; request interceptor adds `Authorization: Bearer {_token}`; response interceptor on HTTP 401 → `clearToken()` + `window.location.replace('/login')` — Exception: the 401 interceptor must NOT redirect when the request is to `/login`; detect by checking `error.config?.url?.includes('/login')` in the interceptor; 401 from `/login` is handled inline by the Login page
    - `useAuth.js`: `login(username, password)` calls `POST /login`; `logout()` calls `clearToken()`; exposes `role` and `isAuthenticated`
    - `useApi.js`: wraps axios calls; on error sets error state and clears page data; network timeout 10 seconds
    - _Requirements: 19.2, 19.4, 19.6, 19.7_

  - [ ] 5.2 Implement ProtectedRoute, RoleRoute, and router setup
    - `ProtectedRoute`: synchronous check on mount; `navigate('/login', {replace:true})` before any protected content renders; transition < 1 second
    - `RoleRoute`: checks JWT `role` claim; redirects to `/login` if role does not match
    - `main.jsx`: routes: `/login` (public), `/analyst/*` (ProtectedRoute + `business_analyst` check), `/engineer/*` (ProtectedRoute + `db_engineer` check), `/` → role-based redirect
    - _Requirements: 19.4_

  - [ ] 5.3 Implement Login page
    - Form with username/password fields; `POST /login` on submit
    - On success: `setToken(token)` + redirect to role-appropriate dashboard
    - On HTTP 401: display inline error message within 3 seconds; do NOT trigger the global 401 redirect handler; no server error details exposed
    - _Requirements: 19.1, 19.6, 19.7_

  - [ ] 5.4 HUMAN STEP — Stitch export
    - **HUMAN STEP**: Before wiring pages, the user must place the Stitch design export in `frontend/src/stitch_export/`; if the export is not usable React, describe what conversion is needed and propose minimal conversion steps; do not redesign screens; agent must stop and wait for confirmation
    - _Requirements: 19.5_

  - [ ] 5.5 Implement analyst EventCalendar page
    - Fetch `GET /events`; display events with name, date range, type
    - On event selection: `GET /forecast/business/{event_id}`; display projected revenue impact, date range, confidence tier
    - API error → error message within 3 seconds; clear stale data
    - _Requirements: 10.1, 10.2, 10.3, 10.5, 10.6_

  - [ ] 5.6 Implement analyst MarginDecomposition page
    - Inputs: segment dropdown (options: Productivity and Business Processes, Intelligent Cloud, More Personal Computing), period_a (text), period_b (text), optional group_by; calls `GET /margin` with group_by passed as query param
    - Render Recharts bar chart with segment-level decomposition (revenue effect, margin-rate effect)
    - _Requirements: 15.3_

  - [ ] 5.7 Implement analyst BusinessForecast page
    - `GET /forecast/business/{event_id}`; display event name, date range, projected revenue impact, confidence tier
    - Optional `as_of_date` date picker that appends `?as_of_date=YYYY-MM-DD` to request
    - _Requirements: 8.1_

  - [ ] 5.8 Implement analyst SqlConsole and MyQueries pages
    - `SqlConsole`: textarea for SQL input; `POST /sql` on submit with 60-second timeout (`{timeout: 60000}` on the axios config); display result rows in table; show "flagged" badge if `flagged=true`; on HTTP 422 display validation error; on timeout display 'Query timed out' error message; response is immediate (background index task is in progress)
    - `MyQueries`: `GET /my-queries`; table with columns: qe_id, started_at, duration_ms, flagged (badge), index status, before_ms / after_ms (display "N/A" if null)
    - _Requirements: 16.1, 16.2, 19.8_

  - [ ] 5.9 Implement engineer WorkloadMonitor and SlowQueryQueue pages
    - `WorkloadMonitor`: `GET /workload`; display top 20 queries table (digest_text, count_star, avg_latency)
    - `SlowQueryQueue`: `GET /slow-queries`; on row selection `GET /slow-queries/{qe_id}/plan`; render plan nodes in tree; highlight "Table scan on" nodes with `pct_time > 50` in a distinct color
    - _Requirements: 7.10_

  - [ ] 5.10 Implement engineer IndexApproval page
    - `GET /index-recs`; display table: table_name, columns_csv, before_ms, after_ms, status
    - Approve button → `POST /index-recs/{id}/approve`; if HTTP 409 display 'Index test not yet complete'; Reject button → `POST /index-recs/{id}/reject`
    - On success: update status in UI without full page reload; API error → error message within 3 seconds, preserve existing table state
    - _Requirements: 7.7, 7.9, 7.8_

  - [ ] 5.11 Implement engineer PartitionManager and CapacityForecast pages
    - `PartitionManager`: `GET /partitions`; display partition name, ordinal, row count, size in MB; form with table_name and boundary date inputs; submit calls `POST /partitions`
    - `CapacityForecast`: `GET /capacity/forecast`; display days_to_90pct, current utilization %; at-risk query list (using AT_RISK_FACTOR from backend); storage warning banner if projected utilization > 80% of DB_ALLOCATED_MB; optional as_of_date input
    - _Requirements: 9.2, 10.4, 14.3, 14.4_

  - [ ] 5.12 Implement engineer EngineerEventCalendar and ConflictLog pages
    - `EngineerEventCalendar`: `GET /engineer/events` (v_events_min — name, dates, expected_lift only, NO revenue columns); on event selection navigate to CapacityForecast for that event_id
    - `ConflictLog`: `GET /conflicts`; display table: conflict_id, rule_id, attempted_by, stale_version, current_version, at
    - _Requirements: 10.1, 10.4, 17.6_

  - [ ] 5.13 Wire Stitch-export components to live API (required)
    - Import components from `stitch_export/`; do not modify layout or styles of exported components
    - Replace any mock data arrays or static constants with `useApi` hook calls to appropriate endpoints
    - Wire Recharts in MarginDecomposition to `GET /margin` response
    - _Requirements: 19.5_

  - [ ] 5.14 Stage 5 gate
    - `npm run build`; assert exit 0 with no errors
    - **HUMAN STEP**: Start backend (`uvicorn backend.app.main:app --reload`) and frontend (`npm run dev`) in separate terminals; verify in browser:
      1. Navigate to `/analyst/events` without logging in → redirects to `/login` within 1 second
      2. Log in as `analyst_demo` → Event Calendar shows events; `v_vm_workload` is accessible; `v_financials` returns MPC rows only
      3. Log in as `engineer_demo` → engineer dashboard loads; no revenue data, no pricing data visible
      4. `POST /sql` with Heavy_Demo_Query (scanning AZURE_VM via `v_vm_workload`) → flagged badge appears; poll MyQueries page until index recommendation appears
      5. Engineer approves index → index visible in PartitionManager after polling
    - Write `docs/stage_5_report.md`
    - **git commit**: `git add -A && git commit -m "Stage 5: frontend complete"`
    - _Requirements: 22.1, 22.2_

---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP; they implement correctness properties as property-based tests
- Each task references specific requirements for traceability
- Checkpoints within stages (e.g., 1.10, 2.12, 3.14, 4.12, 5.14) are gate tasks — all preceding tasks in that stage must pass before proceeding
- HUMAN STEP tasks (0.0, 1.0-style explicit labels) require operator action and agent must pause
- Property tests (3.11, 3.12, 3.13, 4.9, 4.10) use the `hypothesis` library from `etl/requirements.txt`
- `CURRENT_USER()` must never appear in any stored procedure, trigger, or function body — always use `SUBSTRING_INDEX(USER(),'@',1)`
- `conn.commit()` must be called after every write stored procedure call via pyodbc
- The `call_sp_out` helper must drain all intermediate result sets with `cursor.nextset()` before reading OUT params
- The `call_sp` helper must also drain trailing result sets with `while cursor.nextset(): pass` after `fetchall()`
- CURRENT_ROLE() may return 'NONE' (treat as empty) or backtick-wrapped names (strip backticks before comparison)
- 01_ddl.sql uses CREATE TABLE IF NOT EXISTS only; use sql/reset_db.sql for destructive resets
- mysqldump backups are written to `~/margin_db_backups/` (outside repo)
- AT_RISK_FACTOR defaults to 1.25 (seeded in CONFIG); used in sp_forecast_database and test_demo_loop.py

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["0.0"] },
    { "id": 1, "tasks": ["0.1", "0.2"] },
    { "id": 2, "tasks": ["0.3", "0.4"] },
    { "id": 3, "tasks": ["0.5"] },
    { "id": 4, "tasks": ["1.1", "1.1b", "1.2"] },
    { "id": 5, "tasks": ["1.3", "1.4"] },
    { "id": 6, "tasks": ["1.5", "1.6"] },
    { "id": 7, "tasks": ["1.7", "1.8"] },
    { "id": 8, "tasks": ["1.9"] },
    { "id": 9, "tasks": ["1.10"] },
    { "id": 10, "tasks": ["2.1"] },
    { "id": 11, "tasks": ["2.2"] },
    { "id": 12, "tasks": ["2.3", "2.4"] },
    { "id": 13, "tasks": ["2.5", "2.6", "2.7"] },
    { "id": 14, "tasks": ["2.8"] },
    { "id": 15, "tasks": ["2.9"] },
    { "id": 16, "tasks": ["2.10"] },
    { "id": 17, "tasks": ["2.11"] },
    { "id": 18, "tasks": ["2.12"] },
    { "id": 19, "tasks": ["3.1", "3.2", "3.3", "3.4", "3.5"] },
    { "id": 20, "tasks": ["3.6", "3.7", "3.8", "3.9", "3.10"] },
    { "id": 21, "tasks": ["3.11", "3.12", "3.13"] },
    { "id": 22, "tasks": ["3.14"] },
    { "id": 23, "tasks": ["4.1"] },
    { "id": 24, "tasks": ["4.2", "4.3"] },
    { "id": 25, "tasks": ["4.4"] },
    { "id": 26, "tasks": ["4.5", "4.6", "4.7"] },
    { "id": 27, "tasks": ["4.8", "4.9", "4.10"] },
    { "id": 28, "tasks": ["4.11"] },
    { "id": 29, "tasks": ["4.12"] },
    { "id": 30, "tasks": ["5.1", "5.2"] },
    { "id": 31, "tasks": ["5.3"] },
    { "id": 32, "tasks": ["5.4"] },
    { "id": 33, "tasks": ["5.5", "5.6", "5.7", "5.8"] },
    { "id": 34, "tasks": ["5.9", "5.10", "5.11", "5.12"] },
    { "id": 35, "tasks": ["5.13"] },
    { "id": 36, "tasks": ["5.14"] }
  ]
}
```
