# Requirements Document

## Introduction

Margin-DB is a full-stack database-intelligence demonstration system built on MySQL 8.0. It models Microsoft's Xbox/Gaming business unit using real SEC EDGAR financial data, synthetic sales transactions, and real-world game-launch events. The system provides business analysts with margin-decomposition, revenue-forecast, and order-placement capabilities; it provides database engineers with query-monitoring, index-recommendation, partition-management, and capacity-forecast capabilities. A staged six-phase development process gates each phase with automated and manual verification.

## Glossary

- **System**: The full Margin-DB application, including database, ETL, backend API, and frontend.
- **ADMIN_USER**: The MySQL account with GRANT OPTION used to run all migrations and own all stored routine definitions.
- **business_analyst**: The MySQL role assigned to analyst demo accounts; grants access to views and analytic stored procedures.
- **db_engineer**: The MySQL role assigned to engineer demo accounts; grants access to intelligence tables, forecasts, and maintenance procedures.
- **role_telemetry**: The MySQL role assigned to the service account that writes telemetry rows.
- **svc_telemetry**: The MySQL service-account user that holds the `role_telemetry` role and is used by the backend to write QUERY_EXECUTION, QUERY_PLAN_NODE, and INDEX_RECOMMENDATION rows.
- **analyst_demo**: Demo account with `business_analyst` role, restricted to the Gaming segment.
- **analyst_all**: Demo account with `business_analyst` role, access to all segments.
- **engineer_demo**: Demo account with `db_engineer` role.
- **ODBC_Layer**: The pyodbc-based database connection layer in `backend/app/db.py`.
- **ETL_Pipeline**: The collection of Python scripts and shell orchestrators in `etl/`.
- **Telemetry_Service**: The `telemetry.py` module that runs EXPLAIN ANALYZE, records query plans, and triggers index recommendations.
- **Heavy_Demo_Query**: A SELECT query against `v_sales` spanning at least four fiscal quarters, joining to at least one dimension table, and grouping by at least two columns; must take > 3 seconds against the full dataset.
- **USER_SEGMENT_MAP**: A table mapping database usernames to allowed segment IDs, used by segment-filtered views.
- **CONFIG**: A two-column key-value table (`config_key`, `config_value`) for runtime configuration.
- **DB_ALLOCATED_MB**: The CONFIG value representing the storage budget for the database, set by the ETL pipeline after the bulk load completes.
- **AT_RISK_FACTOR**: The CONFIG value used as the multiplier threshold for marking a query as at-risk in the DB load forecast; defaults to 1.25 if absent.
- **as_of_date**: An optional date parameter passed as a MySQL session variable to forecast stored procedures, defaulting to CURDATE().

---

## Requirements

### Requirement 1: Role-Based Authentication and Authorization

**User Story:** As a user of the system, I want to authenticate with my database credentials over ODBC, so that the backend can enforce role-based access without storing plaintext passwords.

#### Acceptance Criteria

1. WHEN a client submits credentials to `POST /login`, THE System SHALL open a pyodbc connection to MySQL using those credentials as the ODBC connect-as-user.
2. WHEN the ODBC connection succeeds, THE System SHALL issue a signed JWT with `{"sub": username, "role": role, "exp": now+3600}` using `pyjwt`.
3. WHEN determining the user's role, THE System SHALL call `SELECT CURRENT_ROLE()`; IF the result is empty, THE System SHALL parse `SHOW GRANTS` to extract the role name; WHEN `SELECT CURRENT_ROLE()` returns the literal string `'NONE'` (MySQL default when no role is set) or a role name wrapped in backticks, THE System SHALL treat `'NONE'` as empty and SHALL strip backticks from role names before comparing to known role values.
4. THE System SHALL store the (username, password) pair in an in-memory credential map keyed by JWT token; THE System SHALL never write credentials to disk or to log output.
5. WHEN a JWT is expired or missing, THE System SHALL return HTTP 401.
6. WHEN credentials are incorrect and the ODBC connection fails, THE System SHALL return HTTP 401.
7. WHEN a valid JWT is present but the caller's role does not match the required role for an endpoint, THE System SHALL return HTTP 403.
8. THE System SHALL provide demo accounts: `analyst_demo` (business_analyst, Gaming segment only), `analyst_all` (business_analyst, all segments), `engineer_demo` (db_engineer).
9. THE System SHALL never log or expose plaintext passwords in responses, logs, or error messages.

---

### Requirement 2: Least-Privilege Role Enforcement

**User Story:** As a security architect, I want each role to have exactly the minimum privileges required for its function, so that no role can access data outside its scope.

#### Acceptance Criteria

1. THE System SHALL grant the `business_analyst` role SELECT on `v_sales`, `v_financials`, `v_events`, `v_forecast_business`, and `v_my_queries`; the views `v_sales` and `v_financials` are segment-filtered via `USER_SEGMENT_MAP` and `SUBSTRING_INDEX(USER(),'@',1)`; `v_my_queries` is filtered to the calling user by `db_user = SUBSTRING_INDEX(USER(),'@',1)` (not by segment); `v_events` and `v_forecast_business` are not filtered.
2. THE System SHALL grant the `business_analyst` role EXECUTE on `sp_margin_decomposition`, `sp_forecast_business`, `sp_update_pricing_rule`, and `sp_place_order`.
3. THE System SHALL grant the `db_engineer` role SELECT on `QUERY_EXECUTION`, `QUERY_PLAN_NODE`, `INDEX_RECOMMENDATION`, `DB_SIZE_SNAPSHOT`, `CONFLICT_LOG`, `v_events_min`, `v_forecast_database`, and `v_partitions`; SELECT on `performance_schema`; EXECUTE on `sp_apply_index`, `sp_reject_index`, `sp_add_partition`, `sp_snapshot_sizes`, and `sp_forecast_database`; SHOW VIEW on all `v_*` views. The `db_engineer` role does NOT have an explicit GRANT on `information_schema` — MySQL allows all authenticated users to query `information_schema` by default.
4. THE System SHALL grant the `business_analyst` role SHOW VIEW on all `v_*` views.
5. THE System SHALL deny SELECT on `ORDERS`, `ORDER_ITEM`, `FACT_SALES`, `PRICING_RULE`, and `QUARTERLY_FINANCIALS` to all demo user accounts; a denied query SHALL return MySQL error 1142.
6. THE System SHALL grant the `role_telemetry` role INSERT and UPDATE on `QUERY_EXECUTION`, `QUERY_PLAN_NODE`, and `INDEX_RECOMMENDATION`; and EXECUTE on `sp_recommend_index`, `sp_insert_qe`, `sp_insert_qpn`, `sp_record_index_test`, and `sp_flag_query`.
7. THE System SHALL restrict `analyst_demo` to the Gaming segment only via `USER_SEGMENT_MAP`; queries through segment-filtered views SHALL return only rows matching the Gaming segment.
8. THE System SHALL assign roles to users with `GRANT <role> TO <user>` statements.
9. THE System SHALL issue `SET DEFAULT ROLE ALL TO <user>` for all demo users and `svc_telemetry`.
10. THE System SHALL place all GRANT statements exclusively in `sql/07_grants.sql`; no GRANT statements SHALL appear in any other migration file.

---

### Requirement 3: Database Schema and Reference Data

**User Story:** As a developer, I want a complete, version-controlled database schema that applies cleanly from scratch, so that any team member can reproduce the environment.

#### Acceptance Criteria

1. THE System SHALL apply seven migration files in order: `01_ddl.sql`, `02_roles.sql`, `03_views.sql`, `04_routines.sql`, `05_triggers_events.sql`, `06_seed_reference.sql`, `07_grants.sql`; a non-zero exit from any file SHALL halt the migration without running subsequent files. The DDL file `01_ddl.sql` SHALL use only `CREATE TABLE IF NOT EXISTS` without any `DROP TABLE` statement; destructive resets are handled by `sql/reset_db.sql` only. THE System SHALL provide a separate `sql/reset_db.sql` script that drops and recreates all objects; this script MUST be run with explicit operator confirmation (prompt: 'Type YES to reset') and SHALL NOT be invoked by `run_migrations.sh`.
2. THE System SHALL define all 24 tables: `COMPANY_SEGMENT`, `DIM_DATE`, `DIM_DEVICE`, `DIM_REGION`, `DIM_CHANNEL`, `PRODUCT`, `USER_SEGMENT_MAP`, `BUSINESS_EVENT`, `STEAM_PLAYER`, `EVENT_WINDOW_DAY`, `QUARTERLY_FINANCIALS`, `GOOGLE_TREND`, `FACT_SALES`, `ORDERS`, `ORDER_ITEM`, `PRICING_RULE`, `AUDIT_LOG`, `CONFLICT_LOG`, `QUERY_EXECUTION`, `QUERY_PLAN_NODE`, `INDEX_RECOMMENDATION`, `DB_SIZE_SNAPSHOT`, `FORECAST`, `FORECAST_LINE`; the `CONFIG` table SHALL have columns `config_key VARCHAR(64)` as PRIMARY KEY and `config_value VARCHAR(255) NOT NULL`. `DB_SIZE_SNAPSHOT` SHALL have a UNIQUE constraint on `snapped_at` to prevent duplicate daily snapshots.
3. THE System SHALL define four weak entities with composite primary keys: `ORDER_ITEM` (order_id, line_no) with owner `ORDERS`; `EVENT_WINDOW_DAY` (event_id, day_offset) with owner `BUSINESS_EVENT`; `QUERY_PLAN_NODE` (qe_id, node_id) with owner `QUERY_EXECUTION`; `FORECAST_LINE` (forecast_id, line_no) with owner `FORECAST`.
4. THE System SHALL define FK constraints on all tables except `FACT_SALES`.
5. FACT_SALES SHALL be partitioned at creation with 20 named quarterly partitions `p_FY23Q1` through `p_FY27Q4` covering 2022-07-01 to 2027-07-01 plus a `p_catch` partition with MAXVALUE; `FACT_SALES.is_synthetic` SHALL be `BOOLEAN NOT NULL DEFAULT TRUE`.
6. THE System SHALL define `INDEX_RECOMMENDATION` with an `index_name VARCHAR(128)` column.
7. THE System SHALL seed reference data before running ETL; reference data includes `COMPANY_SEGMENT`, `DIM_DATE` (2022-07-01 to 2027-06-30), `DIM_DEVICE`, `DIM_REGION`, `DIM_CHANNEL`, `PRODUCT`, `USER_SEGMENT_MAP`, and an initial `CONFIG` row for `DB_ALLOCATED_MB`. THE System SHALL seed at least 100 products total, distributed across segments, to provide sufficient variety for realistic sales data generation.
8. THE System SHALL use `DROP TRIGGER IF EXISTS` + `CREATE TRIGGER` and `DROP PROCEDURE IF EXISTS` + `CREATE PROCEDURE` patterns (not `CREATE OR REPLACE`) for all trigger and procedure definitions; a DDL error SHALL halt migration.
9. ALL GRANT statements SHALL appear only in `07_grants.sql`; no GRANT statement SHALL appear in `01_ddl.sql` through `06_seed_reference.sql`.

---

### Requirement 4: ETL Pipeline

**User Story:** As a data engineer, I want a reproducible, fault-tolerant ETL pipeline, so that the database is populated with real financial and market data and realistic synthetic sales.

#### Acceptance Criteria

1. THE ETL_Pipeline SHALL fetch Microsoft financials from SEC EDGAR using URL `https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json` with a `User-Agent` header read from the `SEC_USER_AGENT` environment variable; non-200 responses SHALL log UTC timestamp + URL and exit with code 1. THE ETL_Pipeline SHALL derive Q4 values where not directly reported as follows: Q4 = FY annual value minus the sum of Q1+Q2+Q3; alternatively, Q4 data may be sourced from Microsoft quarterly earnings releases; the derivation method SHALL be documented in `docs/MARGIN_DB.md`.
2. THE ETL_Pipeline SHALL load `STEAM_PLAYER` rows from `data/raw/steam_players.csv`; the `compute_event_windows.py` script SHALL run separately after `load_events.py` and SHALL process only `game_launch` events; for each such event, it SHALL compute `EVENT_WINDOW_DAY` offsets -14 to +30, where `lift_ratio = avg_players_on_day / AVG(avg_players_14_days_before_start_date)` for each event day; `AVG(lift_ratio)` SHALL only be computed when at least one player data point exists (IS NOT NULL); `EVENT_WINDOW_DAY` SHALL NOT be populated by `load_steam.py`.
3. THE ETL_Pipeline SHALL load Google Trends data via `pytrends`; WHEN `TooManyRequestsError` is raised, THE ETL_Pipeline SHALL fall back to `data/raw/trends_manual.csv` and log the rate-limit event; if neither source is available, THE ETL_Pipeline SHALL log and exit 1.
4. THE ETL_Pipeline SHALL validate each event row: `source_url` must be non-null and non-empty; `end_date` must be >= `start_date`; `event_type` must match an ENUM value; failed rows SHALL be rejected individually with a log entry; the file load SHALL not batch-abort on a single bad row.
5. THE ETL_Pipeline SHALL check that at least one `game_launch` event with `start_date` after the current date exists at load time; if none exists, THE ETL_Pipeline SHALL log a warning and document the `as_of_date` route to exercise the forecast endpoint; operators must provide events.csv with at least one future `game_launch` event or document the `as_of_date` workaround.
6. THE ETL_Pipeline SHALL generate 2 million to 5 million `FACT_SALES` rows at Daily_Aggregate grain for FY23Q1 through FY26Q4 only; rows are `is_synthetic=TRUE` (the column default); reconciliation SHALL compare `SUM(gross_amount)` per `(fiscal_year, fiscal_quarter, segment_id)` to `QUARTERLY_FINANCIALS.revenue` within 0.1%; THE ETL_Pipeline SHALL reconcile FACT_SALES revenue against three COMPANY_SEGMENT groupings: (1) Gaming, (2) More Personal Computing excluding Gaming (if segment data requires it), and (3) Productivity and Business Cloud; each segment SHALL be reconciled independently to avoid double-counting total versus segment rows; after `LOAD DATA LOCAL INFILE` completes, THE ETL_Pipeline SHALL insert into CONFIG: `('DB_ALLOCATED_MB', computed_value)` where `computed_value = (current_mb + 25 × slope_mb_per_day) / 0.9`, current_mb is the total data+index MB from `information_schema` at post-load time, and slope_mb_per_day is estimated as 1% of current_mb per day (a labelled simulation); this value represents a simulated budget such that 90% fills in approximately 25 days at the estimated growth rate; the synthetic RNG SHALL be seeded from the `SYNTHETIC_SEED` environment variable (default 42); the 90-day DB_SIZE_SNAPSHOT backfill SHALL anchor the NEWEST row to the current actual total (data+index) MB from `information_schema` at backfill runtime.
7. THE ETL_Pipeline SHALL deduplicate QUARTERLY_FINANCIALS restatements by keeping the latest `filed_date` per `(period, metric)`; THE ETL_Pipeline SHALL load only quarterly frames (not annual or TTM); THE ETL_Pipeline SHALL require all three metrics (Revenue, COGS, Operating Income) for a period.
8. THE ETL_Pipeline SHALL be orchestrated by `load_sales.sh`; a 30-minute timeout wrapper SHALL halt the entire pipeline on any step failure; `set -euo pipefail` SHALL be set.
9. THE ETL_Pipeline SHALL use `mysql-connector-python` for all direct DB writes; pyodbc SHALL NOT be used in ETL scripts.
10. THE ETL_Pipeline SHALL implement a partial-commit guard: each script commits in batches; if interrupted mid-script, a re-run SHALL use `INSERT IGNORE` or `ON DUPLICATE KEY UPDATE` to resume without duplicate rows.
11. THE ETL_Pipeline SHALL define a Heavy_Demo_Query that spans a date range of at least four fiscal quarters, joins `FACT_SALES` (via `v_sales`) to at least one dimension table, and groups by at least two columns; the query must take more than 3 seconds against the full dataset with no secondary index. THE Stage 2 gate verification SHALL run the Heavy_Demo_Query via the MySQL CLI connecting as `analyst_all` (not via the backend API); the operator SHALL manually create the candidate index (`CREATE INDEX idx_stage2_test ON FACT_SALES (product_id, sale_date)`), verify >= 50% speedup by rerunning the query, and then drop the index (`DROP INDEX idx_stage2_test ON FACT_SALES`) before proceeding to Stage 3.
12. THE ETL_Pipeline HUMAN STEPs require the operator to provide: `data/raw/segments.csv`, `data/raw/steam_players.csv`, `data/raw/events.csv`, the MySQL ODBC 8.0 Unicode Driver, and `.env` passwords; the agent SHALL stop and wait for each.

---

### Requirement 5: Stored Routines

**User Story:** As a developer, I want all business logic encapsulated in stored procedures and triggers, so that the logic is enforced regardless of the calling application.

#### Acceptance Criteria

1. THE System SHALL define all stored procedures and triggers in `04_routines.sql` and `05_triggers_events.sql` using the `DROP IF EXISTS` + `CREATE` pattern rather than `CREATE OR REPLACE` for maximum compatibility with MySQL 8.0.18.
2. THE System SHALL declare all stored procedures and functions with `SQL SECURITY DEFINER` and owned by `ADMIN_USER`.
3. THE System SHALL define scheduled events: `ev_snapshot_sizes` (daily, calls `sp_snapshot_sizes`) and `ev_recalc_forecasts` (nightly, loops through BUSINESS_EVENT calling `sp_forecast_database`); enabling `event_scheduler=ON` is a HUMAN STEP recorded in Stage 0 setup and SHALL NOT appear in any migration file.
4. THE System SHALL use MySQL 8.0 syntax only in all stored procedures; Oracle PL/SQL constructs (`:=` operator, `DBMS_*` packages, `DECLARE...EXCEPTION` blocks, `RAISE_APPLICATION_ERROR`, bare `EXCEPTION` keyword) SHALL NOT appear in any stored procedure, function, or trigger body; `CURRENT_USER()` SHALL NOT appear in any stored procedure, function, or trigger body.
5. WHEN a stored procedure is called and the named procedure does not exist, THE System SHALL return a MySQL error (not an empty result set).
6. WHEN a SIGNAL is raised inside a stored procedure, THE ODBC_Layer SHALL propagate the rollback to the caller.
7. WHEN a runtime exception occurs inside a stored procedure that uses an EXIT HANDLER, THE System SHALL roll back uncommitted work and RESIGNAL the exception to the caller.
8. WHEN any stored procedure or function makes a write to a table (INSERT, UPDATE, DELETE), THE ODBC_Layer SHALL call `conn.commit()` after the `{CALL}` statement; WHEN a stored procedure uses OUT parameters, THE ODBC_Layer SHALL drain all result sets using `nextset()` before executing `SELECT @out0, @out1, ...` to read the OUT values; THE ODBC_Layer SHALL also call `nextset()` in a loop inside `call_sp()` to drain any trailing result sets before closing the cursor; WHEN a stored procedure returns no result set, THE ODBC_Layer SHALL handle the absence of `cursor.description` without raising an exception.
9. THE System SHALL use `SUBSTRING_INDEX(USER(),'@',1)` wherever the calling user's identity is needed inside any stored procedure, function, or trigger body.
10. WHEN `sp_margin_decomposition` or `sp_forecast_business` is called, THE System SHALL intersect the requested segment parameter with the caller's `USER_SEGMENT_MAP` entries; IF the requested segment is not in the caller's allowed segments, THE System SHALL return an empty result set rather than raising an error.

---

### Requirement 6: Backend API

**User Story:** As a frontend developer, I want a FastAPI backend that exposes analyst and engineer endpoints, so that the React frontend can call well-defined REST endpoints.

#### Acceptance Criteria

1. THE System SHALL implement the backend using FastAPI and uvicorn under `backend/app/`; all database connections SHALL use pyodbc via ODBC only.
2. THE System SHALL implement the analyst router with endpoints: `GET /events`, `GET /margin?segment&period_a&period_b&group_by`, `GET /forecast/business/{event_id}`, `POST /sql`, `PUT /pricing/{rule_id}`, `GET /my-queries`, `POST /analyst/orders`.
3. THE System SHALL implement the engineer router with endpoints: `GET /workload`, `GET /slow-queries`, `GET /slow-queries/{qe_id}/plan`, `GET /index-recs`, `POST /index-recs/{id}/approve`, `POST /index-recs/{id}/reject`, `GET /partitions`, `POST /partitions`, `GET /capacity/forecast`, `POST /forecast/database/{event_id}`, `GET /conflicts`, `GET /engineer/events`.
4. THE System SHALL read all connection parameters from environment variables; no hardcoded connection strings SHALL appear in source code.
5. THE System SHALL return HTTP 422 on malformed request bodies; THE System SHALL return HTTP 503 when the database is unavailable.
6. THE System SHALL serve the OpenAPI docs at `GET /docs`.
7. THE System SHALL not run as the system root user; the startup check SHALL verify this.
8. THE System SHALL configure CORS to allow only the Vite development origin (`http://localhost:5173`).

---

### Requirement 7: Query Monitoring, Telemetry, Index Workflow

**User Story:** As a database engineer, I want the system to automatically detect slow queries, recommend indexes, and verify their effectiveness, so that I can approve improvements with a single click.

#### Acceptance Criteria

1. WHEN a query is submitted via `POST /sql`, THE Telemetry_Service SHALL run `EXPLAIN ANALYZE` on that query using a separate execution to obtain the plan tree; for each plan node, `actual_ms` SHALL be the per-loop end time extracted from `actual time=N..M loops=L` (i.e., M, the elapsed time for one loop iteration); `total_ms` SHALL be `actual_ms × loops`; `pct_time` SHALL be computed as `total_ms / root_total_ms × 100`; `duration_ms` for the `QUERY_EXECUTION` row SHALL be the root node's `actual_ms` (the per-loop end time of the root node, not wall-clock time); actual result rows SHALL be obtained from a separate plain execution of the query.
2. THE Telemetry_Service SHALL read `performance_schema` digest statistics only for supplemental query metadata; EXPLAIN ANALYZE SHALL be the source of truth for plan data.
3. THE Telemetry_Service SHALL write `QUERY_EXECUTION` rows via the `svc_telemetry` connection using `sp_insert_qe` (with OUT parameter `p_qe_id`); `QUERY_PLAN_NODE` rows SHALL be written via `sp_insert_qpn`.
4. THE Telemetry_Service SHALL flag a query execution if any plan node has `operation` beginning with "Table scan on" AND `pct_time > 50`; `telemetry.py` SHALL extract the target table and candidate columns for the flag rule on the analyst connection.
5. THE System SHALL whitelist the target table name against a list of allowed tables before constructing the `ALTER TABLE` statement in `sp_recommend_index`; each column name SHALL match the regex `^\w+$` and SHALL exist in `information_schema.COLUMNS` for the target table.
6. THE System SHALL call `sp_recommend_index(IN p_qe_id BIGINT, IN p_table VARCHAR(64), IN p_columns_csv VARCHAR(500), OUT p_rec_id INT)` which SHALL: deduplicate by checking for an existing active recommendation for the same (table, columns_csv); insert an `INDEX_RECOMMENDATION` row before creating the index; use `LAST_INSERT_ID()` to obtain `rec_id`; then UPDATE `index_name = CONCAT('idx_rec_', rec_id)`; SET `p_rec_id = rec_id`; the `ALTER TABLE` statement SHALL create the index as INVISIBLE: `ALTER TABLE {table} ADD INDEX {index_name} ({columns}) INVISIBLE, ALGORITHM=INPLACE`.
7. THE index creation and before/after performance test SHALL execute as a FastAPI background task after `POST /sql` returns; `POST /sql` SHALL return immediately with `{qe_id, flagged, rows, duration_ms}` without waiting for the index recommendation to complete; the background task measures `before_ms` (without invisible index, baseline run) and `after_ms` (with `SET SESSION optimizer_switch='use_invisible_indexes=on'`) and calls `sp_record_index_test`.
8. AN Index_Recommendation SHALL only be approvable (i.e., `POST /index-recs/{id}/approve` SHALL only succeed) when `after_ms` is non-NULL in `INDEX_RECOMMENDATION`; IF `after_ms IS NULL`, THE System SHALL return HTTP 409 with a message 'Index test not yet complete'.
9. THE System SHALL define `sp_flag_query(IN p_qe_id BIGINT)` which sets `flagged=1` on the QUERY_EXECUTION row for `p_qe_id`; this procedure is called by `telemetry.py` via the `svc_telemetry` connection when the flag condition is met; `role_telemetry` has EXECUTE on this procedure.
10. WHEN an engineer calls `POST /index-recs/{id}/approve`, THE System SHALL call `sp_apply_index` which sets the index to VISIBLE and updates `status='applied'`.
11. WHEN an engineer calls `POST /index-recs/{id}/reject`, THE System SHALL call `sp_reject_index` which drops the index and updates `status='rejected'`.
12. THE System SHALL expose slow query data via `GET /slow-queries` ordered by `started_at` descending; `GET /slow-queries/{qe_id}/plan` SHALL return `QUERY_PLAN_NODE` rows for that execution.
13. THE System SHALL set `MAX_EXECUTION_TIME=30000` on every analyst session connection opened for `POST /sql`.
14. WHEN the index recommendation background task runs, `test_demo_loop.py` SHALL poll `GET /index-recs` until a recommendation for the relevant `qe_id` appears, up to 60 seconds.

---

### Requirement 8: Business Forecast

**User Story:** As a business analyst, I want to see a revenue forecast for an upcoming game-launch event, so that I can estimate the business impact before the event occurs.

#### Acceptance Criteria

1. THE System SHALL compute a trailing four-quarter average daily revenue as the baseline for `sp_forecast_business`.
2. THE System SHALL join `EVENT_WINDOW_DAY` lift ratios to the event's window when available; WHEN `EVENT_WINDOW_DAY` data is not available, THE System SHALL fall back to `BUSINESS_EVENT.expected_lift`.
3. THE System SHALL compute `event_lift = AVG(lift_ratio over the event window days) − 1`; this uses player-count lift as a proxy for revenue lift; the proxy assumption (Steam player engagement correlates with revenue) SHALL be documented in `docs/MARGIN_DB.md`; `projected_revenue_impact = baseline_daily_revenue × event_duration_days × event_lift`.
4. THE System SHALL assign a confidence tier: `high` when all three inputs are available (baseline, event_lift from window, event dates), `medium` when two are available, `low` when zero or one are available.
5. THE System SHALL return an empty result set (not an error) when no qualifying data exists.
6. THE System SHALL accept an `as_of_date` parameter via MySQL session variable `@as_of_date`; NULL SHALL default to `CURDATE()`.
7. THE System SHALL not hardcode any revenue constants; all values SHALL be derived from QUARTERLY_FINANCIALS and EVENT_WINDOW_DAY.

---

### Requirement 9: DB Load Forecast

**User Story:** As a database engineer, I want to see a projection of when the database will approach its storage limit, so that I can plan partition additions and hardware upgrades in advance.

#### Acceptance Criteria

1. THE System SHALL compute estimated rows, MB growth, and days to 90% of `DB_ALLOCATED_MB` using linear regression on the last 90 days of `DB_SIZE_SNAPSHOT`.
2. THE System SHALL display a 30-day warning when `days_to_90` falls below 30.
3. THE System SHALL mark a query as at-risk if its projected execution duration is >= CONFIG.AT_RISK_FACTOR × its current baseline, where AT_RISK_FACTOR is read from the CONFIG table (default 1.25); `projected_duration_ms = baseline_ms × (projected_rows / current_rows)`.
4. THE System SHALL order forecast results by event date.
5. THE System SHALL schedule nightly recalculation via `ev_recalc_forecasts`.
6. THE System SHALL accept an `as_of_date` parameter via MySQL session variable.
7. WHEN the computed growth slope is <= 0, THE System SHALL set `days_to_90 = NULL` and write an `error_indicator` flag to `FORECAST_LINE`; THE System SHALL NOT raise an exception in this case.
8. THE System SHALL read `DB_ALLOCATED_MB` from `CONFIG WHERE config_key = 'DB_ALLOCATED_MB'`; IF the CONFIG row is absent, THE System SHALL fall back to the `@db_allocated_mb` session variable.
9. `sp_forecast_database` SHALL compute storage growth during the event as `slope_mb_per_day × (1 + event_lift)` for each day in the event window, and projected_rows as `current_rows + rows_per_day × event_duration_days × (1 + event_lift)`.

---

### Requirement 10: Event Calendar UI

**User Story:** As a user, I want to view an event calendar that shows upcoming game-launch events, so that I can initiate forecasts from a visual timeline.

#### Acceptance Criteria

1. WHEN an analyst accesses the Event Calendar, THE System SHALL fetch events via `GET /events` which reads from `v_events`; WHEN an engineer accesses the Event Calendar, THE System SHALL fetch events via `GET /engineer/events` which reads from `v_events_min`.
2. THE System SHALL display each event's name, date range, and type.
3. WHEN an analyst selects an event, THE System SHALL navigate to the Business Forecast view for that event.
4. WHEN an engineer selects an event, THE System SHALL navigate to the DB Load Forecast view for that event; THE System SHALL display a storage warning if projected utilization exceeds 80% of `DB_ALLOCATED_MB`.
5. WHEN an API call returns an error, THE System SHALL display an error message and SHALL NOT render stale data.
6. THE System SHALL not hardcode event data or forecast values in the frontend; all data SHALL come from API responses.

---

### Requirement 11: End-to-End Demo Loop

**User Story:** As a demonstrator, I want a scripted end-to-end demo that shows query flagging, index recommendation, and performance improvement, so that stakeholders can see the system's intelligence capabilities live.

#### Acceptance Criteria

1. THE System SHALL support the Heavy_Demo_Query being submitted by the analyst via `POST /sql`; the query targets `v_sales` (not `FACT_SALES` directly); `v_sales` exposes `platform`, `device_family`, `oem`, `region_name`, and `channel_name` columns for grouping.
2. WHEN the Heavy_Demo_Query is flagged, THE Telemetry_Service SHALL apply the flag rule (Requirement 7.4) and enqueue an index recommendation as a background task.
3. THE System SHALL allow the engineer to view the slow-query queue via `GET /slow-queries`.
4. WHEN the engineer approves the recommendation via `POST /index-recs/{id}/approve`, THE System SHALL apply the index within 30 seconds; the index SHALL be verifiable in `information_schema.STATISTICS` via the admin connection.
5. WHEN the analyst reruns the Heavy_Demo_Query after the index is applied, THE System SHALL record `duration_ms` values in `QUERY_EXECUTION` that are at least 50% lower than the `duration_ms` from step 1; `test_demo_loop.py` SHALL compare `duration_ms` values from API responses, not wall-clock timing.
6. THE System SHALL complete all demo-loop steps (submit query, detect recommendation, approve index, verify index, verify speedup, generate DB forecast, verify capacity) via `test_demo_loop.py` within 120 seconds; `test_demo_loop.py` SHALL drop any prior test index at startup for idempotency; `test_demo_loop.py` SHALL poll `GET /index-recs` for the recommendation up to 60 seconds; `test_demo_loop.py` SHALL call `POST /forecast/database/{event_id}` to generate a DB forecast before calling `GET /capacity/forecast`; all assertion logic SHALL complete within 120 seconds.

---

### Requirement 12: Optimistic Locking

**User Story:** As a business analyst, I want to update pricing rules without overwriting concurrent changes, so that data integrity is preserved in multi-user scenarios.

#### Acceptance Criteria

1. THE System SHALL implement `sp_update_pricing_rule(IN p_rule_id, IN p_new_value, IN p_expected_version, OUT p_status)` with an optimistic lock: `UPDATE PRICING_RULE ... WHERE rule_id=p_rule_id AND version=p_expected_version`.
2. WHEN `ROW_COUNT() = 1`, THE System SHALL set `p_status = 'OK'` and increment the version.
3. WHEN `ROW_COUNT() = 0`, THE System SHALL insert a row into `CONFLICT_LOG` and set `p_status = 'CONFLICT'`; THE System SHALL NOT raise a SIGNAL on version mismatch.
4. THE System SHALL define AFTER UPDATE and AFTER DELETE triggers on `PRICING_RULE` that insert into `AUDIT_LOG` with old and new values as JSON, `db_user = SUBSTRING_INDEX(USER(),'@',1)`, and `at = NOW(3)`.
5. WHEN `PUT /pricing/{rule_id}` is called and `sp_update_pricing_rule` returns `p_status = 'OK'`, THE System SHALL return HTTP 200; WHEN `p_status = 'CONFLICT'`, THE System SHALL return HTTP 409.

---

### Requirement 13: Transactional Order Placement

**User Story:** As a business analyst, I want to place test orders that write atomically to ORDERS, ORDER_ITEM, and FACT_SALES, so that the demo can show transaction integrity.

#### Acceptance Criteria

1. THE System SHALL implement `sp_place_order` using an EXIT HANDLER + RESIGNAL pattern; `JSON_TABLE` SHALL iterate the items JSON array; the savepoint names SHALL be `sp_orders`, `sp_item`, and `sp_fact`; no partial rows SHALL remain after a rollback.
2. THE System SHALL expose `POST /analyst/orders` for test and demo purposes only; this endpoint is not intended for production use.

---

### Requirement 14: Partition Management

**User Story:** As a database engineer, I want to add quarterly partitions to FACT_SALES without downtime, so that the table never hits the MAXVALUE catch-all partition.

#### Acceptance Criteria

1. FACT_SALES SHALL be partitioned at creation with 20 named quarterly partitions plus `p_catch MAXVALUE`.
2. THE System SHALL implement `sp_add_partition` using `REORGANIZE PARTITION` to split the `p_catch` partition into a new named partition plus a new `p_catch`; dynamic SQL via PREPARE/EXECUTE/DEALLOCATE PREPARE SHALL be used.
3. THE System SHALL expose `GET /partitions` which reads from `v_partitions`.
4. THE System SHALL expose `POST /partitions` which calls `sp_add_partition`.
5. WHEN a query filters FACT_SALES to a single fiscal quarter, an `EXPLAIN` output SHALL show at most 1–2 partitions accessed.

---

### Requirement 15: Margin Decomposition

**User Story:** As a business analyst, I want to decompose the margin change between two periods into volume, price, discount, and mix effects, so that I can understand the drivers of profitability changes.

#### Acceptance Criteria

1. THE System SHALL implement `sp_margin_decomposition(IN p_segment VARCHAR(50), IN p_period_a VARCHAR(10), IN p_period_b VARCHAR(10))`; the procedure SHALL accept an optional session variable `@group_by` that, if set, controls additional grouping in the output; the procedure SHALL return a result set with columns `(effect_name, value_delta)`.
2. THE System SHALL expose `GET /margin?segment&period_a&period_b&group_by` which calls `sp_margin_decomposition`; the `group_by` parameter SHALL be passed as a session variable `SET @group_by = ?` before calling the stored procedure.
3. THE System SHALL render the margin decomposition result as a Recharts bar chart in the frontend.

---

### Requirement 16: SQL Console

**User Story:** As a business analyst, I want a SQL console where I can run ad-hoc SELECT queries against views, so that I can explore data beyond the pre-built dashboards.

#### Acceptance Criteria

1. THE System SHALL accept `POST /sql` with a request body containing a single `SELECT` or a single `WITH...SELECT`; THE System SHALL reject any SQL string that contains a semicolon followed by a non-whitespace character (multi-statement) before opening a database connection; THE System SHALL reject any DML keyword at a word boundary (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`) in the SQL string.
2. THE System SHALL run the SQL on the analyst's ODBC connection against `v_*` views; THE System SHALL return `{qe_id, flagged, rows, duration_ms}` immediately; the index recommendation background task runs after the response is sent.
3. THE System SHALL expose `GET /my-queries` which reads `v_my_queries` filtered to the current user, returning columns: `qe_id`, `started_at`, `duration_ms`, `flagged`, index status, `before_ms`, `after_ms`.
4. THE System SHALL set `MAX_EXECUTION_TIME=30000` on every analyst session used by `POST /sql`.

---

### Requirement 17: DB_SIZE_SNAPSHOT and Capacity Tracking

**User Story:** As a database engineer, I want daily size snapshots and a capacity forecast, so that I can track growth trends and anticipate storage limits.

#### Acceptance Criteria

1. THE System SHALL implement `sp_snapshot_sizes` which executes `SET SESSION information_schema_stats_expiry=0` before inserting into `DB_SIZE_SNAPSHOT` from `information_schema.TABLES` where `TABLE_SCHEMA = DATABASE()`.
2. THE System SHALL schedule `ev_snapshot_sizes` as a daily event that calls `sp_snapshot_sizes`.
3. THE 90-day backfill (`etl/backfill_snapshots.py`) SHALL anchor the NEWEST row to the current actual total (data+index) MB from `information_schema` at backfill runtime; earlier synthetic rows SHALL be extrapolated backwards as `current_mb − (slope × days_back)` with a floor of 1 MB; all backfill rows SHALL carry `is_synthetic=TRUE` and SHALL be labelled as simulated in any documentation.
4. THE System SHALL implement `sp_forecast_database` which reads `DB_ALLOCATED_MB` from `CONFIG WHERE config_key = 'DB_ALLOCATED_MB'`, performs linear regression on the last 90 days, and writes results to `FORECAST` and `FORECAST_LINE`.
5. THE System SHALL expose `GET /capacity/forecast` and `POST /forecast/database/{event_id}`.
6. THE System SHALL expose `GET /conflicts` which returns `CONFLICT_LOG` rows ordered by `at` descending with a limit of 100.

---

### Requirement 18: Testing

**User Story:** As a developer, I want automated tests for roles, concurrency, ODBC connectivity, and the end-to-end demo loop, so that regressions are caught before each stage gate.

#### Acceptance Criteria

1. THE System SHALL provide `tests/run_role_tests.sh`: a shell script that logs in as each demo user and asserts MySQL error 1142 on denied queries; asserts row counts for segment-filtered views; uses an `assert_denied()` function and an `assert_row_count()` function; does not require a running backend.
2. THE System SHALL provide `tests/test_concurrency.py` which: (a) reads the current `version` of a known `rule_id` from PRICING_RULE before spawning threads; (b) launches two threads that simultaneously submit `PUT /pricing/{rule_id}` with that version as `expected_version`; (c) asserts exactly one thread receives HTTP 200 and the other receives HTTP 409; (d) asserts exactly one CONFLICT_LOG row for that rule_id was created; cleans up at teardown.
3. THE System SHALL provide `tests/test_demo_loop.py`: executes all demo-loop steps; drops any prior test index first; compares `duration_ms` from POST /sql responses (not wall-clock timing); calls `POST /forecast/database/{event_id}` before `GET /capacity/forecast`; polls for recommendation up to 60 seconds; verifies index existence via admin connection; completes within 120 seconds total.
4. THE System SHALL provide `tests/test_odbc.py`: connects within 10 seconds; runs a parameterized query; calls at least one stored procedure; ODBC driver not found → fail immediately with driver name printed.
5. ALL test failures SHALL write to stdout/stderr with the test name, assertion description, and actual vs expected values.
6. WHEN an ODBC timeout occurs during test execution, THE System SHALL fail the test immediately rather than hanging.

---

### Requirement 19: Frontend

**User Story:** As a user, I want a React single-page application that routes analyst and engineer users to role-appropriate dashboards, so that each persona sees only their relevant screens.

#### Acceptance Criteria

1. THE System SHALL implement the frontend using React 18 and Vite.
2. THE System SHALL centralize all API calls in `src/api/client.js`; no component SHALL call `fetch` or `axios` directly.
3. THE System SHALL route analyst pages under `/analyst/` and engineer pages under `/engineer/`.
4. WHEN a user without a valid token navigates to a protected route, THE System SHALL redirect to `/login` within 1 second.
5. THE System SHALL place Stitch design exports in `frontend/src/stitch_export/` without altering the layout or styles of exported components.
6. WHEN an API call returns an error, THE System SHALL display an error message within 3 seconds and SHALL NOT render stale data; page state SHALL be preserved on non-fatal errors.
7. WHEN a user is on the `/login` page, THE System SHALL NOT redirect on a 401 response from `POST /login`; the 401 SHALL be displayed as an inline error message instead of triggering the global 401 redirect handler.
8. THE frontend `POST /sql` call SHALL use a client-side timeout of 60 seconds; IF the request times out, THE System SHALL display a 'Query timed out' error message.

---

### Requirement 20: Configuration and Hygiene

**User Story:** As a developer, I want a clean repository configuration, so that secrets are not committed and the project is reproducible.

#### Acceptance Criteria

1. THE System SHALL provide `.env.example` with all required variables including: `DB_HOST`, `DB_PORT`, `DB_NAME`, `ADMIN_USER`, `ADMIN_PASSWORD`, `SVC_TELEMETRY_USER`, `SVC_TELEMETRY_PASSWORD`, `SEC_USER_AGENT`, `JWT_SECRET`, `DB_ALLOCATED_MB`, `DEMO_ANALYST_PASSWORD`, `DEMO_ANALYST_ALL_PASSWORD`, `DEMO_ENGINEER_PASSWORD`, `SYNTHETIC_SEED`, `ANALYST_DEMO_USERNAME`, `ANALYST_ALL_USERNAME`, `ENGINEER_DEMO_USERNAME`; each variable SHALL have a comment and a placeholder value.
2. THE System's `.gitignore` SHALL ignore `data/clean/`, `data/raw/msft_facts.json`, `.venv/`, `dist/`, and `*.sql` dump files only (not all of `data/raw/`); it SHALL include `!data/raw/events.csv` and `!data/raw/segments.csv` to force-include those committed files; it SHALL also ignore `.env`, `__pycache__/`, `*.pyc`, and `node_modules/`; mysqldump backup files SHALL be written outside the repository (e.g., to `~/margin_db_backups/`) to avoid large binary files in git history.
3. THE System SHALL provide `README.md` with: prerequisites (Python 3.12, Node 18, MySQL 8.0.18+, MySQL ODBC 8.0 Unicode Driver, unixODBC) with minimum versions; setup steps; stage-by-stage run instructions; demo loop instructions; concrete command examples; a note explaining MySQL stored procedure syntax differences from Oracle PL/SQL (MySQL uses `DELIMITER`, no `DECLARE...EXCEPTION`, no `DBMS_*` packages, no `:=` operator); platform notes with `uname -m`.
4. THE System SHALL write a `Stage_Report` document after each stage gate recording build commands, test output, open issues, and verification results.
5. THE System's README SHALL include `uname -m` platform notes and a note on the four weak entities in the schema.

---

### Requirement 21: ETL Data Validation

**User Story:** As a data engineer, I want the ETL scripts to validate input data before inserting, so that bad records are logged and skipped rather than silently corrupting the database.

#### Acceptance Criteria

1. THE ETL_Pipeline SHALL validate each record's fields against the target schema before inserting; fields that do not match the expected type, range, or non-null constraint SHALL be rejected.
2. WHEN a record fails validation, THE ETL_Pipeline SHALL log: the record identifier (row number or primary key), the field name, the expected format or range, and the actual value.
3. WHEN an input file is malformed, missing, or empty, THE ETL_Pipeline SHALL halt the affected script and log the reason; it SHALL NOT insert partial data from a fatally invalid file.

---

### Requirement 22: Staged Development Process

**User Story:** As a project lead, I want the development to proceed through six gated stages, so that each layer is verified before the next is built.

#### Acceptance Criteria

1. THE System SHALL follow six stages: Stage 0 (Environment), Stage 1 (Schema), Stage 2 (ETL), Stage 3 (Stored Routines), Stage 4 (Backend API), Stage 5 (Frontend); each stage SHALL end with a gate that must be passed before the next stage begins.
2. THE System SHALL produce a `Stage_Report` document after each gate recording commands, test output, and open issues.
3. THE System SHALL commit to git at every stage gate; the commit message SHALL follow the format `"Stage N: <description>"`.
4. Before the Stage 0 gate, THE System SHALL execute a HUMAN STEP in which the user logs in to MySQL as root and: (a) creates the `margin_db` database (`CREATE DATABASE IF NOT EXISTS margin_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`); (b) creates the ADMIN_USER account with `ALL ON margin_db.* WITH GRANT OPTION`, `CREATE USER, CREATE ROLE, ROLE_ADMIN ON *.*`, and `SELECT ON performance_schema.* WITH GRANT OPTION`; (c) sets `event_scheduler=ON` and `local_infile=1`; (d) updates `.env` with `ADMIN_USER`, `ADMIN_PASSWORD`, and `DB_NAME=margin_db`.
5. WHEN a HUMAN STEP is encountered, THE System SHALL stop and wait for operator confirmation before proceeding; HUMAN STEPs include: providing `segments.csv`, `steam_players.csv`, `events.csv`, installing the ODBC driver, and setting `.env` passwords.
6. THE System SHALL prompt the operator to run `mysqldump` before Stage 3 begins; the exact command SHALL be provided in the gate checklist; the dump file SHALL be written to `~/margin_db_backups/` outside the repository.
7. THE System SHALL stop on any MySQL conflict during migrations and ask the operator before retrying; destructive commands SHALL require explicit operator confirmation.
