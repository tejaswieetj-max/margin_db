# Margin DB: Master Spec (for Kiro)

Work in stages. At the end of each stage: run its checks, summarize results, STOP, and wait for my approval before the next stage. Where a step says HUMAN STEP, do not attempt it; tell me exactly what to do and wait.

## 0. Project summary
DBMS course project on a single corporation: Microsoft (Gaming focus). Business events (Call of Duty launch, Game Pass promo, Black Friday, price change) drive BOTH a business forecast (revenue/margin) and a database load forecast (storage, query degradation). Two logins: `business_analyst` and `db_engineer`.

Demo loop that must work end to end:
1. Analyst runs a heavy query on FACT_SALES (slow, full scan).
2. System flags it (full scan node > 50% of plan time) and shows it in the engineer's queue.
3. Engineer approves the recommended index.
4. Analyst reruns the same query and it is measurably faster.
5. Event calendar shows a Call of Duty event raising the DB forecast ("storage hits 90% in N days, these queries degrade").

## 1. Fixed stack (do not substitute)
- OS: macOS (Apple Silicon or Intel; detect with `uname -m`). Shell: zsh.
- MySQL 8.0.18+ already installed locally, reachable from the terminal with `mysql`. Required: `performance_schema=ON`, `local_infile` enabled.
- Python 3.12 in a venv at `.venv`. Node LTS for the frontend.
- Backend: FastAPI + uvicorn. DB access via **pyodbc with MySQL Connector/ODBC** (ODBC is a course requirement). No ORM. Raw SQL and stored procedures called as `{CALL sp_name(?, ?)}`.
- `mysql-connector-python` is allowed ONLY in ETL scripts under `etl/`, never in `backend/`.
- Procedural logic: MySQL stored procedures, functions, triggers, EVENTs (no Oracle PL/SQL syntax).
- Frontend: React 18 + Vite. UI screens are generated in Stitch and exported by me; you wire them to the API.

## 2. Repo layout
```
margin-db/
  .env.example  .gitignore  README.md
  data/raw/  data/clean/
  sql/01_ddl.sql  sql/02_roles.sql  sql/03_views.sql  sql/04_routines.sql  sql/05_triggers_events.sql  sql/06_seed_reference.sql
  etl/  (fetch_edgar.py, load_financials.py, load_steam.py, load_trends.py, load_events.py, gen_synthetic_sales.py, load_sales.sh)
  backend/  (app/main.py, app/db.py, app/auth.py, app/telemetry.py, app/routers/analyst.py, app/routers/engineer.py)
  tests/  (test_roles.sql, test_concurrency.py, test_demo_loop.py, test_odbc.py)
  frontend/  (src/api/client.js, src/stitch_export/, src/pages/analyst/, src/pages/engineer/)
  docs/
```
`.env` is git-ignored. `.env.example` lists: `DB_HOST DB_PORT DB_NAME ADMIN_USER ADMIN_PASSWORD SVC_TELEMETRY_USER SVC_TELEMETRY_PASSWORD SEC_USER_AGENT JWT_SECRET`.

## 3. STAGE 0: Environment
Run and verify each:
1. `brew install unixodbc` then `odbcinst -j` (record config file paths).
2. HUMAN STEP: I download MySQL Connector/ODBC (macOS, matching `uname -m`) from dev.mysql.com and install it. After I say done, locate the Unicode driver: `find /usr/local /opt -name "libmyodbc8w*" 2>/dev/null`. If macOS quarantines it: `xattr -d com.apple.quarantine <path>`.
3. Register it in the `odbcinst.ini` path from step 1 as `[MySQL ODBC 8.0 Unicode Driver]` with `Driver=<found path>`. Verify `odbcinst -q -d`.
4. Create venv and install, then freeze to `requirements.txt`:
   `pip install fastapi "uvicorn[standard]" pyodbc mysql-connector-python pandas numpy requests python-dotenv pydantic pytest pytrends pyjwt python-multipart`
5. `tests/test_odbc.py`: connects via `DRIVER={MySQL ODBC 8.0 Unicode Driver}` and runs `SELECT VERSION()`.
6. Check `SELECT VERSION()` >= 8.0.18, `performance_schema` ON, `local_infile` ON (if OFF, tell me the exact `SET GLOBAL` to run; connect with `--local-infile=1`).
7. `npm create vite@latest frontend -- --template react`, `npm install`, `npm install axios react-router-dom recharts`.
8. HUMAN STEP: I provide MySQL root password in `.env`.

Gate: test_odbc passes, versions confirmed.

## 4. STAGE 1: Schema, roles, views
Create database `margin_db`. Use InnoDB, utf8mb4.

Business tables:
- COMPANY_SEGMENT(segment_id PK, name)
- PRODUCT(product_id PK, segment_id FK, name, category, list_price)
- DIM_DATE(date_key PK, full_date, year, quarter, month, is_black_friday, is_holiday)
- DIM_DEVICE(device_id PK, platform, device_family, oem NULL). platform in (Xbox console, Windows PC, Mac, Mobile, Cloud); oem only for PC rows.
- DIM_REGION(region_id PK, name), DIM_CHANNEL(channel_id PK, name)
- ORDERS(order_id PK, order_date, device_id FK, region_id FK, channel_id FK, status)
- ORDER_ITEM **WEAK**, owner ORDERS: PK(order_id, line_no), product_id FK, qty, unit_price, discount, unit_cost
- FACT_SALES **partitioned**: PK(sale_id, sale_date), sale_date, product_id, segment_id, device_id, region_id, channel_id, event_id NULL, qty, gross_amount, discount_amount, cost_amount, is_synthetic. `PARTITION BY RANGE (TO_DAYS(sale_date))` with at least 8 quarterly partitions plus a MAXVALUE partition. **No FKs on it and no FK children** (MySQL restriction); enforce references with triggers.
- PRICING_RULE(rule_id PK, product_id FK, rule_type, value, version INT NOT NULL DEFAULT 1, updated_by, updated_at)
- BUSINESS_EVENT(event_id PK, event_type, name, start_date, end_date, expected_lift, source_url)
- EVENT_WINDOW_DAY **WEAK**, owner BUSINESS_EVENT: PK(event_id, day_offset), players_index, lift_ratio
- QUARTERLY_FINANCIALS PK(fiscal_year, fiscal_quarter, segment_id): revenue, cogs, operating_income, source
- STEAM_PLAYER(app_id, obs_date, avg_players, peak_players)
- FORECAST(forecast_id PK, kind ENUM('business','database'), event_id, created_at, created_by)
- FORECAST_LINE **WEAK**, owner FORECAST: PK(forecast_id, line_no), metric, period, value

DB-intelligence tables:
- QUERY_EXECUTION(qe_id PK, db_user, digest_text, started_at, duration_ms, rows_examined, rows_sent, flagged)
- QUERY_PLAN_NODE **WEAK**, owner QUERY_EXECUTION: PK(qe_id, node_id), parent_node_id, operation, table_name, pct_time, est_rows, actual_rows
- INDEX_RECOMMENDATION(rec_id PK, qe_id FK, table_name, columns_csv, status ENUM('proposed','testing','approved','rejected','applied'), before_ms, after_ms, decided_by, decided_at)
- DB_SIZE_SNAPSHOT(snap_id PK, taken_at, table_name, data_mb, index_mb, row_count)
- CONFLICT_LOG(conflict_id PK, rule_id, attempted_by, stale_version, current_version, at)
- USER_SEGMENT_MAP(db_user, segment_id)
- AUDIT_LOG(audit_id PK, table_name, action, old_value, new_value, db_user, at)

Weak entities required: ORDER_ITEM, QUERY_PLAN_NODE, FORECAST_LINE.

Accounts and roles (`sql/02_roles.sql`):
- Roles `business_analyst`, `db_engineer`. Demo users `analyst_demo` (segment: Gaming only) and `engineer_demo`, plus `analyst_all` (all segments).
- `svc_telemetry` service account: INSERT/UPDATE on QUERY_EXECUTION, QUERY_PLAN_NODE, INDEX_RECOMMENDATION and EXECUTE on `sp_recommend_index` only. The backend uses it to record telemetry, because analysts must not have access to those tables.
- business_analyst: SELECT only on views `v_sales`, `v_financials`, `v_events`, `v_forecast_business`, filtered to the caller's segments via `USER_SEGMENT_MAP` and `SUBSTRING_INDEX(USER(),'@',1)`; views `SQL SECURITY DEFINER`. EXECUTE on `sp_margin_decomposition`, `sp_forecast_business`, `sp_update_pricing_rule`.
- db_engineer: SELECT on QUERY_EXECUTION, QUERY_PLAN_NODE, INDEX_RECOMMENDATION, DB_SIZE_SNAPSHOT, CONFLICT_LOG, views `v_events_min` (name, dates, lift only) and `v_forecast_database`; read on `information_schema`/`performance_schema`; EXECUTE on `sp_apply_index`, `sp_reject_index`, `sp_add_partition`, `sp_snapshot_sizes`, `sp_forecast_database`. **No access to** ORDERS, ORDER_ITEM, FACT_SALES, PRICING_RULE, QUARTERLY_FINANCIALS, or any revenue/price/cost/device column.
- Set default roles with `SET DEFAULT ROLE`. Never use the root account from the backend.

Gate (`tests/test_roles.sql`, run as each user): analyst cannot read QUERY_PLAN_NODE or INDEX_RECOMMENDATION; engineer cannot read FACT_SALES or ORDERS; `analyst_demo` sees only Gaming rows; partitioning shows pruning in `EXPLAIN` for a one-quarter query (test after Stage 2 data load).

## 5. STAGE 2: Data acquisition and loading
Real data. Scripts under `etl/`, reading config from `.env`. Every script is idempotent and logs row counts.

1. `fetch_edgar.py`: GET `https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json` with header `User-Agent: $SEC_USER_AGENT` (SEC rejects requests without one). Save to `data/raw/msft_facts.json`. `load_financials.py` extracts quarterly (not cumulative or annual) Revenue, Cost of revenue, Operating income into QUARTERLY_FINANCIALS. Dedupe restatements by taking the latest filed value per period. Print any missing quarters instead of guessing.
2. HUMAN STEP: segment split. I provide `data/raw/segments.csv` (fiscal_year, fiscal_quarter, segment, revenue, operating_income where disclosed, source_url) transcribed from Microsoft's filings. Do not fill this from memory. Validate it and load.
3. HUMAN STEP: Steam players. I place a CSV in `data/raw/steam_players.csv` (app_id, obs_date, avg_players, peak_players) from a Kaggle dataset or SteamCharts export. `load_steam.py` validates and loads, then computes EVENT_WINDOW_DAY (day offsets -14..+30 relative to each event start, `lift_ratio` = players / pre-event 14-day baseline).
4. `load_trends.py`: pytrends for "Call of Duty", "Game Pass", "Xbox price"; on rate-limit failure, accept a manual CSV in `data/raw/`.
5. HUMAN STEP: events. I fill `data/raw/events.csv` (event_type, name, start_date, end_date, expected_lift, source_url), 40-60 rows. You provide a template with 3 example rows and a validator that rejects rows without `source_url` or with end_date < start_date. Loader: `load_events.py`.
6. `gen_synthetic_sales.py` (synthetic, by my decision): generates 2-5M rows for FACT_SALES (plus matching ORDERS/ORDER_ITEM for a 100k-row subset) with `is_synthetic=1`. Rules:
   - For each real fiscal quarter, scale generated amounts so the quarter's synthetic revenue equals the real revenue for that segment in QUARTERLY_FINANCIALS (reconciliation).
   - Apply event lifts from EVENT_WINDOW_DAY during event windows.
   - Seasonality: Black Friday and holiday spikes.
   - Device mix across DIM_DEVICE; OEM only on Windows PC rows.
   - Output CSV chunks to `data/clean/`, load with `LOAD DATA LOCAL INFILE` via `load_sales.sh`.
   - Seeded RNG for reproducibility. Print a reconciliation report (synthetic vs real per quarter, must be within 0.1%).
7. Seq-scan check (critical): without any secondary index on `(product_id, sale_date)`, run the heavy demo query and show `EXPLAIN` using a full scan, with runtime above 3 seconds. If not, increase row count or adjust the query and tell me.

Gate: row counts per table, reconciliation report, the seq-scan evidence.

## 6. STAGE 3: Stored routines, triggers, events
(`sql/04_routines.sql`, `sql/05_triggers_events.sql`; test each in the MySQL shell before any API exists.)
1. `sp_margin_decomposition(segment, period_a, period_b)`: revenue and margin change split into volume, price, discount, and mix effects.
2. `sp_forecast_business(event_id)`: applies event lift to baseline; writes FORECAST + FORECAST_LINE.
3. `sp_forecast_database(event_id)`: takes growth from DB_SIZE_SNAPSHOT history, multiplies write rate by the event lift during its window, runs a linear regression with SUM/COUNT formulas, writes days-to-90%-of-allocated-storage and the digests expected to degrade.
4. `sp_update_pricing_rule(rule_id, new_value, expected_version)`: optimistic lock. `UPDATE ... WHERE rule_id=? AND version=?`; if `ROW_COUNT()=0`, insert CONFLICT_LOG and `SIGNAL SQLSTATE '45000'`; else version+1.
5. `sp_place_order(...)`: `START TRANSACTION`, insert ORDERS, ORDER_ITEM, FACT_SALES with SAVEPOINTs; on error `ROLLBACK TO` the savepoint and log.
6. `sp_recommend_index(qe_id)`: rule-based. If a full-scan node over the threshold exists, take filter/join columns from the plan and create the index `INVISIBLE`, status `testing`. Document honestly in comments that it is heuristic.
7. `sp_apply_index(rec_id)`: set `VISIBLE`, status `applied`, audit row. `sp_reject_index(rec_id)`: drop the invisible index, status `rejected`.
8. `sp_add_partition(table, boundary)`: `REORGANIZE PARTITION` on the MAXVALUE partition.
9. `sp_snapshot_sizes()` reads `information_schema.TABLES` into DB_SIZE_SNAPSHOT; `EVENT ev_snapshot_sizes` runs it daily (enable `event_scheduler`).
10. Triggers: audit on PRICING_RULE update/delete; integrity checks on FACT_SALES insert (product_id/segment_id exist).
11. Growth history (synthetic, by my decision): a script backfills 90 days of DB_SIZE_SNAPSHOT with a plausible growth curve flagged as synthetic, so the regression has data. Live snapshots take over afterward.

Gate (SQL-level tests): concurrent edit conflict, failed order leaves no partial rows, forecast output sanity.

## 7. STAGE 4: Backend (FastAPI + pyodbc)
- `app/db.py`: pyodbc connection helper. Connection string uses `DRIVER={MySQL ODBC 8.0 Unicode Driver}`. Calls via `{CALL sp(?,?)}`. Handle multiple result sets.
- Auth: `POST /login` takes MySQL username and password, verifies by opening an ODBC connection as that user, determines role from `SHOW GRANTS`/role membership, returns a JWT. Credentials are held server-side in an in-memory session map keyed by the token and never written to disk or logs. Every request opens its own connection as that user, so MySQL enforces permissions, not app code.
- Telemetry wrapper (`app/telemetry.py`) for analyst SQL: run `EXPLAIN ANALYZE <query>` on the analyst's connection; parse the tree output (indentation = depth) into nodes; execute the query and time it; then, using the `svc_telemetry` connection, insert QUERY_EXECUTION and QUERY_PLAN_NODE rows; store the normalized digest text, not literal values. If any full-scan node exceeds 50% of total time, set `flagged=1` and call `sp_recommend_index`.
- `POST /sql` accepts SELECT only (reject anything else before executing; the DB grants are the second layer) and runs against `v_*` views.
- For testing a recommended index, use a single session: `SET SESSION optimizer_switch='use_invisible_indexes=on'`, rerun the query, store before/after ms in INDEX_RECOMMENDATION.
- Analyst endpoints: GET /events, GET /margin?segment&period_a&period_b, GET /forecast/business/{event_id}, POST /sql, PUT /pricing/{rule_id} (body includes expected_version; 409 on conflict), GET /my-queries.
- Engineer endpoints: GET /workload, GET /slow-queries, GET /slow-queries/{qe_id}/plan, GET /index-recs, POST /index-recs/{id}/approve and /reject, GET /partitions, POST /partitions, GET /capacity/forecast, POST /forecast/database/{event_id}, GET /conflicts.
- CORS for the Vite dev origin only. Consistent error JSON. OpenAPI docs enabled.
- Tests: `test_concurrency.py` (two threads editing one rule), `test_demo_loop.py` (heavy query flagged → approve → faster).

Gate: demo loop passes via API calls (curl or pytest) before any UI work.

## 8. STAGE 5: Frontend integration
- HUMAN STEP: I generate screens in Stitch and place the export in `frontend/src/stitch_export/`. If the export is not usable React, tell me and propose the minimal conversion; do not redesign the screens.
- Build `src/api/client.js` (axios, base URL from env, JWT in memory, interceptor for 401/409). Role-based routing after login: analyst or engineer area only.
- Analyst pages: Event Calendar, Margin Decomposition (recharts), Business Forecast, SQL Console (results table + query status), My Queries (shows flagged status and resolved speedup).
- Engineer pages: Workload Monitor, Slow-Query Queue with plan tree (bottleneck node highlighted), Index Approval (before/after), Partitions, Capacity Forecast (days-to-90%, event driver, at-risk queries), Conflict Log.
- The engineer UI must never render or request business values.
- Replace any mock data from the Stitch export with live API calls.

Gate: full demo loop in the browser with two logins.

## 9. Rules for you (Kiro)
- No invented data anywhere. Real data comes from the sources above; synthetic data is only what Section 5.6 and 6.11 define, always flagged `is_synthetic`.
- Ask me before any destructive command (`DROP DATABASE`, `rm -rf`, `TRUNCATE`).
- Never run the backend as root and never put passwords in code or commits.
- After each stage, write `docs/stage_N_report.md` (what was built, commands run, test output, open issues).
- If a requirement conflicts with a MySQL limitation, stop and tell me instead of silently changing the design.
