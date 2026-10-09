# Margin DB: Project Handoff Document

Single source of truth for any agent or collaborator joining the project. Read fully before acting. Items marked **[OPEN]** are unverified or undecided; do not assume them.

---

## 1. One-paragraph summary

Margin DB is a DBMS course project built around **one corporation: Microsoft (Gaming focus: Xbox, Game Pass, Call of Duty)**. A single **business event calendar** (Call of Duty launch, Game Pass promo, Black Friday, price change) drives **two forecasts at once**: a **business forecast** (revenue and margin shift) and a **database load forecast** (write growth, storage exhaustion date, queries that will degrade). Two role-separated logins, **business analyst** and **database engineer**, each see only their own responsibility. The headline demo is a closed loop: an analyst's slow query is auto-flagged, the engineer approves a recommended index, and the analyst reruns the query and sees the speedup.

## 2. Novelty (why this is not "another sales dashboard")

1. **Events drive both business and database forecasts.** One event calendar produces: "Launch week adds ~X revenue and shifts margin Y" and "That launch pushes FACT_SALES writes up N×; storage hits 90% in D days; these queries will degrade." Few student DBMS projects link business events to workload prediction.
2. **Database intelligence is a product feature, not decoration.** Slow-node detection, index recommendation, partition management and capacity forecasting exist because the business events create real load.
3. **Role separation is enforced by the database itself** (MySQL roles, views, column-level grants), not by application code. The engineer role physically cannot read revenue, price, cost, or device data.
4. **Real telemetry.** Because the synthetic business data is loaded into the real MySQL instance, query plans, timings and sizes captured from `EXPLAIN ANALYZE`, `performance_schema` and `information_schema` are genuine measurements of the system.
5. **Synthetic data is anchored to real data.** Synthetic transactions are scaled so each quarter's total reconciles to Microsoft's real reported revenue (SEC EDGAR) within 0.1%.

## 3. Target audience

| Audience | What they need from the system |
|---|---|
| **Business analyst** (persona) | Revenue/margin decomposition, event-driven business forecast, SQL console on approved views, pricing-rule editing. Scoped to their segment (e.g. Gaming). |
| **Database engineer** (persona) | Workload monitor, slow-query queue with plan trees, index approval, partition management, capacity forecast tied to events, concurrency conflict log. Never sees business values. |
| **Course evaluator** (primary real-world audience) | Visible coverage of DBMS concepts: ER design with weak entities, OLTP/OLAP, SQL, stored routines, triggers, events, partitioning, indexing, query plans, transactions/rollback, optimistic concurrency, roles/privileges, ODBC connectivity. |
| **Project owner** | A defensible, demo-able project with real data where possible and honestly-labelled synthetic data where not. |

## 4. Fixed technology stack (do not substitute)

| Layer | Choice |
|---|---|
| Database | **MySQL 8.0.18+** (needs `EXPLAIN ANALYZE`, roles, invisible indexes). Local install. `performance_schema=ON`, `local_infile` enabled, `event_scheduler=ON`. |
| Procedural language | MySQL stored procedures/functions/triggers/EVENTs (SQL/PSM). **Oracle PL/SQL is not available**; use MySQL stored routines and say so in the report. |
| Backend | Python 3.12, FastAPI, uvicorn |
| DB access (backend) | **pyodbc + MySQL Connector/ODBC** (ODBC is a course requirement). No ORM. Stored procedures called as `{CALL sp_name(?, ?)}`. |
| DB access (ETL only) | `mysql-connector-python`, allowed **only** under `etl/`, never in `backend/` |
| Frontend | React 18 + Vite; screens designed in **Stitch**, exported, then wired to the API |
| Dev machine | macOS (check `uname -m`: arm64 vs x86_64), zsh |
| Agent tooling | Kiro (spec-driven build, **Supervised mode**, not Autopilot); Antigravity for frontend wiring; Devin optional |

## 5. System components

```
Frontend (React/Vite, Stitch screens)
        │  HTTPS/JSON + JWT
Backend (FastAPI)
  ├─ auth.py          login via real MySQL account; JWT; in-memory credential map
  ├─ db.py            pyodbc connection helper, {CALL ...}
  ├─ telemetry.py     EXPLAIN ANALYZE wrapper, plan parser, flagging
  └─ routers/         analyst.py, engineer.py
        │  ODBC (per-user connection, so MySQL enforces access)
MySQL 8
  ├─ business tables + FACT_SALES (partitioned)
  ├─ views (v_*) per role, segment-filtered
  ├─ stored routines, triggers, scheduled EVENTs
  └─ db-intelligence tables (query plans, index recs, size snapshots)
ETL (Python scripts, etl/)
  EDGAR · Steam · Trends · events CSV · synthetic sales generator
```

Key design rule: **the backend connects to MySQL as the logged-in user's own MySQL account**, so privileges are enforced by the database. A separate `svc_telemetry` service account writes telemetry tables on the analyst's behalf (analysts cannot touch those tables).

## 6. Features

### Business analyst
1. **Event calendar** with per-event business forecast (revenue and margin shift, confidence tier based on data coverage).
2. **Margin decomposition** (`sp_margin_decomposition`): change in revenue/margin split into volume, price, discount and mix effects, by segment and period/event window.
3. **SQL console**: SELECT-only, against approved `v_*` views, row-limited to the analyst's segment.
4. **Pricing-rule editor** with **optimistic locking** (version column; stale update rejected, conflict logged, HTTP 409).
5. **My Queries**: status of own queries (flagged, index applied, speedup).

### Database engineer
1. **Workload monitor**: top digests by total time, latency, rows examined vs returned (from `performance_schema`).
2. **Slow-query queue**: auto-flagged queries with the parsed plan tree and highlighted bottleneck node.
3. **Index recommendations**: rule-based recommender creates the index as **INVISIBLE**, tests it in-session (`use_invisible_indexes=on`), records before/after ms; engineer **approves** (flip to VISIBLE) or **rejects** (drop).
4. **Partition management**: partition sizes/skew, add next partition (`REORGANIZE PARTITION`), archive/drop old.
5. **Capacity forecast**: size-growth regression per table with event lift applied; "storage hits 90% in N days"; list of queries projected to degrade.
6. **Concurrency log**: optimistic-lock conflicts, rollback counts.
7. **Maintenance**: daily size-snapshot EVENT, run/schedule jobs.

### Cross-cutting database features
Partitioning (RANGE on date) · indexing and invisible-index testing · query-plan analysis · stored procedures, functions, triggers, EVENTs · transaction with `SAVEPOINT`/`ROLLBACK` (order placement) · explanation of rollback vs undo (InnoDB undo logs) · optimistic concurrency · audit logging · roles, views, column-level grants · ODBC connectivity.

## 7. Demo loop (acceptance story)

1. Analyst runs a heavy query on `FACT_SALES` (unindexed; slow; full scan).
2. Telemetry wrapper runs `EXPLAIN ANALYZE`, parses the plan into `QUERY_PLAN_NODE`; a full-scan node over **50% of plan time** sets `flagged=1` and calls `sp_recommend_index`.
3. Engineer sees the flagged query, plan tree and recommendation; approves; index becomes VISIBLE.
4. Analyst reruns the identical query: at least **50% faster** than the recorded baseline.
5. Event calendar shows a "game_launch" event with the DB forecast warning: days until 90% storage and the queries projected to degrade (projected time at least **2×** baseline).

## 8. Entities and schema

Engine InnoDB, charset utf8mb4. Types are indicative; adjust sensibly.

### 8.1 Business tables

**COMPANY_SEGMENT**: `segment_id TINYINT PK`, `name VARCHAR(50)`
**PRODUCT**: `product_id INT PK AI`, `segment_id FK→COMPANY_SEGMENT`, `name`, `category`, `list_price DECIMAL(10,2)`
**DIM_DATE**: `date_key INT PK (yyyymmdd)`, `full_date DATE`, `year`, `quarter`, `month`, `fiscal_year`, `fiscal_quarter`, `is_black_friday BOOL`, `is_holiday BOOL` (Microsoft's fiscal year ends June 30; FY Q1 = Jul–Sep)
**DIM_DEVICE**: `device_id PK`, `platform` (Xbox console / Windows PC / Mac / Mobile / Cloud), `device_family`, `oem NULL` (only for PC rows; Lenovo/Dell/HP/ASUS etc.). Synthetic dimension.
**DIM_REGION**: `region_id PK`, `name`
**DIM_CHANNEL**: `channel_id PK`, `name`
**ORDERS**: `order_id BIGINT PK`, `order_date`, `device_id FK`, `region_id FK`, `channel_id FK`, `status`
**ORDER_ITEM** *(weak entity, owner ORDERS)*: **PK (order_id, line_no)**, `product_id FK`, `qty`, `unit_price`, `discount`, `unit_cost`
**FACT_SALES** *(partitioned)*: **PK (sale_id, sale_date)**, `sale_id BIGINT AI`, `sale_date DATE`, `product_id`, `segment_id`, `device_id`, `region_id`, `channel_id`, `event_id NULL`, `qty`, `gross_amount`, `discount_amount`, `cost_amount`, `is_synthetic BOOL NOT NULL DEFAULT TRUE`.
`PARTITION BY RANGE (TO_DAYS(sale_date))`, at least 8 quarterly partitions plus a MAXVALUE partition. **No foreign keys on this table and none referencing it** (MySQL restriction on partitioned InnoDB tables); integrity enforced by a BEFORE INSERT trigger.
**PRICING_RULE**: `rule_id PK`, `product_id FK`, `rule_type`, `value`, `version INT NOT NULL DEFAULT 1`, `updated_by`, `updated_at`

### 8.2 Event and forecast tables

**BUSINESS_EVENT**: `event_id PK`, `event_type` (game_launch / subscription_promo / seasonal / price_change / acquisition / outage), `name`, `start_date`, `end_date`, `expected_lift`, `source_url NOT NULL`
**EVENT_WINDOW_DAY** *(weak entity, owner BUSINESS_EVENT)*: **PK (event_id, day_offset)**, `players_index`, `lift_ratio` (real Steam uplift, day offsets −14..+30)
**FORECAST**: `forecast_id PK`, `kind ENUM('business','database')`, `event_id FK`, `created_at`, `created_by`
**FORECAST_LINE** *(weak entity, owner FORECAST)*: **PK (forecast_id, line_no)**, `metric`, `period`, `value`

### 8.3 Real external data tables

**QUARTERLY_FINANCIALS**: **PK (fiscal_year, fiscal_quarter, segment_id)**, `revenue`, `cogs`, `operating_income NULL`, `source`
**STEAM_PLAYER**: `app_id`, `obs_date`, `avg_players`, `peak_players` (PK app_id + obs_date)

### 8.4 Database-intelligence tables

**QUERY_EXECUTION**: `qe_id PK`, `db_user`, `digest_text` (normalized; **no literal values**), `started_at`, `duration_ms`, `rows_examined`, `rows_sent`, `flagged BOOL`
**QUERY_PLAN_NODE** *(weak entity, owner QUERY_EXECUTION)*: **PK (qe_id, node_id)**, `parent_node_id`, `operation`, `table_name`, `pct_time`, `est_rows`, `actual_rows`
**INDEX_RECOMMENDATION**: `rec_id PK`, `qe_id FK`, `table_name`, `columns_csv`, `status ENUM('proposed','testing','approved','rejected','applied')`, `before_ms`, `after_ms`, `decided_by`, `decided_at`
**DB_SIZE_SNAPSHOT**: `snap_id PK`, `taken_at`, `table_name`, `data_mb`, `index_mb`, `row_count`, `is_synthetic`
**CONFLICT_LOG**: `conflict_id PK`, `rule_id`, `attempted_by`, `stale_version`, `current_version`, `at`
**USER_SEGMENT_MAP**: `db_user`, `segment_id`
**AUDIT_LOG**: `audit_id PK`, `table_name`, `action`, `old_value`, `new_value`, `db_user`, `at`

### 8.5 Weak entities (course requirement: at least 2; we have 4)
1. `ORDER_ITEM`, identified by (order_id, line_no), owner `ORDERS`
2. `QUERY_PLAN_NODE`, identified by (qe_id, node_id), owner `QUERY_EXECUTION`
3. `FORECAST_LINE`, identified by (forecast_id, line_no), owner `FORECAST`
4. `EVENT_WINDOW_DAY`, identified by (event_id, day_offset), owner `BUSINESS_EVENT`

Owner tables of weak entities must not be partitioned.

### 8.6 Main relationships
COMPANY_SEGMENT 1—N PRODUCT · ORDERS 1—N ORDER_ITEM · PRODUCT 1—N ORDER_ITEM · BUSINESS_EVENT 1—N EVENT_WINDOW_DAY · BUSINESS_EVENT 1—N FORECAST · FORECAST 1—N FORECAST_LINE · QUERY_EXECUTION 1—N QUERY_PLAN_NODE · QUERY_EXECUTION 1—N INDEX_RECOMMENDATION · PRODUCT 1—N PRICING_RULE · FACT_SALES logically references PRODUCT, segment, device, region, channel, event (enforced by trigger, not FK).

## 9. Roles, accounts, and access

**Accounts:** `analyst_demo` (Gaming segment only), `analyst_all` (all segments), `engineer_demo`, `svc_telemetry` (service). Never connect the backend as root.

**business_analyst role:**
- SELECT **only on views**: `v_sales`, `v_financials`, `v_events`, `v_forecast_business`. Views are `SQL SECURITY DEFINER` and filtered by `USER_SEGMENT_MAP` using `SUBSTRING_INDEX(USER(),'@',1)`. (MySQL has no native row-level security; this is the substitute.)
- EXECUTE: `sp_margin_decomposition`, `sp_forecast_business`, `sp_update_pricing_rule`.
- No access to query-plan, index, size or conflict tables.

**db_engineer role:**
- SELECT on `QUERY_EXECUTION`, `QUERY_PLAN_NODE`, `INDEX_RECOMMENDATION`, `DB_SIZE_SNAPSHOT`, `CONFLICT_LOG`, and views `v_events_min` (name/dates/lift only) and `v_forecast_database`; read access to `information_schema` / `performance_schema`.
- EXECUTE: `sp_apply_index`, `sp_reject_index`, `sp_add_partition`, `sp_snapshot_sizes`, `sp_forecast_database`.
- **No access** to `ORDERS`, `ORDER_ITEM`, `FACT_SALES`, `PRICING_RULE`, `QUARTERLY_FINANCIALS`, or any revenue/price/cost/device column.

**svc_telemetry:** INSERT/UPDATE on `QUERY_EXECUTION`, `QUERY_PLAN_NODE`, `INDEX_RECOMMENDATION`; EXECUTE on `sp_recommend_index` only.

## 10. Stored routines, triggers, events

| Object | Purpose |
|---|---|
| `sp_margin_decomposition(segment, period_a, period_b)` | Volume / price / discount / mix effects |
| `sp_forecast_business(event_id)` | Applies event lift to baseline; writes FORECAST + FORECAST_LINE |
| `sp_forecast_database(event_id)` | Growth from DB_SIZE_SNAPSHOT × event lift; linear regression via SUM/COUNT; writes days-to-90% and at-risk digests |
| `sp_update_pricing_rule(rule_id, new_value, expected_version)` | Optimistic lock: `UPDATE … WHERE version=?`; if `ROW_COUNT()=0` → CONFLICT_LOG + `SIGNAL SQLSTATE '45000'` |
| `sp_place_order(...)` | `START TRANSACTION` + `SAVEPOINT`; `ROLLBACK TO` on failure; inserts ORDERS, ORDER_ITEM, FACT_SALES |
| `sp_recommend_index(qe_id)` | Heuristic rule-based; creates INVISIBLE index, status `testing` |
| `sp_apply_index(rec_id)` / `sp_reject_index(rec_id)` | Make VISIBLE / drop; audit row |
| `sp_add_partition(table, boundary)` | `REORGANIZE PARTITION` on MAXVALUE partition |
| `sp_snapshot_sizes()` | Reads `information_schema.TABLES` into DB_SIZE_SNAPSHOT |
| `EVENT ev_snapshot_sizes` | Runs snapshot daily (also recalculates forecasts, interval ≤ 24h) |
| Triggers | Audit on PRICING_RULE update/delete; integrity checks on FACT_SALES insert |

The recommender is **heuristic**; document it as such in the report. MySQL has no hypothetical-index feature, hence the invisible-index approach.

## 11. API (FastAPI, per-user DB connection)

**Auth:** `POST /login` (verifies by opening an ODBC connection as that MySQL user; returns JWT with role; credentials held server-side in memory only, never logged or persisted).

**Analyst:** `GET /events`, `GET /margin?segment&period_a&period_b`, `GET /forecast/business/{event_id}`, `POST /sql` (SELECT-only), `PUT /pricing/{rule_id}` (body has `expected_version`; 409 on conflict), `GET /my-queries`.

**Engineer:** `GET /workload`, `GET /slow-queries`, `GET /slow-queries/{qe_id}/plan`, `GET /index-recs`, `POST /index-recs/{id}/approve`, `POST /index-recs/{id}/reject`, `GET /partitions`, `POST /partitions`, `GET /capacity/forecast`, `POST /forecast/database/{event_id}`, `GET /conflicts`.

**Telemetry wrapper** (on every analyst query): run `EXPLAIN ANALYZE` on the analyst's connection → parse tree (indentation = depth) into plan nodes → execute and time → using `svc_telemetry`, insert QUERY_EXECUTION + QUERY_PLAN_NODE → flag if a full-scan node exceeds 50% of time → call `sp_recommend_index`. `performance_schema` supplies digest stats only (latency, rows examined, no-index-used); it does **not** provide per-node plan timing.

Other requirements: CORS for the Vite dev origin only; consistent error JSON; OpenAPI `/docs` on; required env vars checked at startup (exit non-zero if missing); never run as OS root.

## 12. Datasets

### 12.1 Real (public)

| Data | Source | Notes |
|---|---|---|
| Microsoft quarterly financials (revenue, COGS, operating income) | SEC EDGAR: `https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json` | **Must send a `User-Agent` header** (name + email) or SEC rejects. Use quarterly, not cumulative/annual; take latest filed value per period for restatements. |
| Gaming / segment revenue split | Microsoft quarterly earnings releases, 10-Q/10-K, IR spreadsheets | **Manual transcription** into `data/raw/segments.csv` (`fiscal_year, fiscal_quarter, segment, revenue, operating_income, source_url`). Gaming is not a standalone reported segment for operating income; leave blank where undisclosed. Microsoft recast its segments in 2024. Never estimate. |
| Steam player counts (Call of Duty) | Kaggle Steam player-count dataset, or steamcharts.com export **[OPEN: verify app ID and terms of use]** | `data/raw/steam_players.csv`. Daily needed for −14/+30 day launch curves; if only monthly exists, use month offsets. |
| Google Trends | `pytrends` for "Call of Duty", "Game Pass", "Xbox price"; manual CSV fallback | Rate-limit prone. |
| Event calendar | **Hand-curated** `data/raw/events.csv`, 40–60 rows | Sources: Wikipedia (CoD release dates), Xbox Wire (Game Pass/console price changes), Black Friday dates, Activision acquisition close (Oct 13, 2023; verify). Every row needs `source_url`. |

### 12.2 Synthetic (explicit project decision)

| Data | Rule |
|---|---|
| Transaction-grain sales (FACT_SALES, ORDERS, ORDER_ITEM) | 2–5M rows so full scans are real. All flagged `is_synthetic=TRUE`. Seeded RNG. **Per quarter per segment, synthetic revenue must reconcile to real QUARTERLY_FINANCIALS within 0.1%** (printed report). Apply real event lifts from EVENT_WINDOW_DAY; Black Friday/holiday seasonality; device mix with OEM only on PC rows. |
| DIM_DEVICE contents (platform/OEM) | Synthetic; Microsoft does not publish device-level sales. |
| 90-day DB_SIZE_SNAPSHOT backfill | Synthetic growth curve flagged `is_synthetic`, so the regression has history; live snapshots take over. |

Everything else (query plans, timings, sizes after load) is **real measurement** from the running MySQL instance.

### 12.3 Known data limits (state honestly in the report)
- Microsoft publishes no transaction-level, per-title, per-device, discount or unit data. Margin analysis on real data is at **segment × quarter** grain.
- Event-to-margin links on real data are quarterly; finer grain exists only in the synthetic layer.
- Real Steam data covers only titles on Steam (Call of Duty since 2022).

## 13. Packages and installs

**macOS (Homebrew):**
```bash
brew install python@3.12 node git unixodbc
```
MySQL 8.0.18+ already installed. Verify:
```bash
mysql -u root -p -e "SELECT VERSION(); SHOW VARIABLES LIKE 'performance_schema'; SHOW VARIABLES LIKE 'local_infile'; SHOW VARIABLES LIKE 'event_scheduler';"
```
Enable `local_infile` / `event_scheduler` with `SET GLOBAL` as needed (user runs these as root).

**MySQL Connector/ODBC (manual download):** dev.mysql.com/downloads/connector/odbc → macOS, matching `uname -m`. Register the Unicode driver in `odbcinst.ini` (find paths with `odbcinst -j`) as `[MySQL ODBC 8.0 Unicode Driver]`; check with `odbcinst -q -d`. If quarantined: `xattr -d com.apple.quarantine <path>`. Common failures: wrong `Driver=` path, driver name mismatch with connection string, architecture mismatch.

**Python (venv at `.venv`):**
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install fastapi "uvicorn[standard]" pyodbc mysql-connector-python pandas numpy requests python-dotenv pydantic pytest pytrends pyjwt python-multipart
pip freeze > requirements.txt
```
`mysql-connector-python` is for `etl/` only.

**Frontend:**
```bash
npm create vite@latest frontend -- --template react
cd frontend && npm install && npm install axios react-router-dom recharts
```

**ODBC connection test:**
```python
import pyodbc
c = pyodbc.connect("DRIVER={MySQL ODBC 8.0 Unicode Driver};SERVER=127.0.0.1;PORT=3306;DATABASE=mysql;UID=root;PWD=...;")
print(c.cursor().execute("SELECT VERSION()").fetchone())
```

**`.env` variables:** `DB_HOST DB_PORT DB_NAME ADMIN_USER ADMIN_PASSWORD SVC_TELEMETRY_USER SVC_TELEMETRY_PASSWORD SEC_USER_AGENT JWT_SECRET` (`.env` git-ignored; commit only `.env.example`).

## 14. Repository layout

```
margin-db/
  .env.example  .gitignore  README.md  requirements.txt
  data/raw/  data/clean/
  sql/01_ddl.sql  02_roles.sql  03_views.sql  04_routines.sql  05_triggers_events.sql  06_seed_reference.sql
  etl/  fetch_edgar.py  load_financials.py  load_steam.py  load_trends.py  load_events.py  gen_synthetic_sales.py  load_sales.sh
  backend/app/  main.py  db.py  auth.py  telemetry.py  routers/analyst.py  routers/engineer.py
  tests/  test_roles.sql  test_concurrency.py  test_demo_loop.py  test_odbc.py
  frontend/src/  api/client.js  stitch_export/  pages/analyst/  pages/engineer/
  docs/  stage_N_report.md
```

## 15. Build process (six stages, each ends with a stop-and-wait gate)

| Stage | Work | Gate (owner reviews) |
|---|---|---|
| 0 | Environment, ODBC, venv, packages | `test_odbc.py` passes; versions confirmed |
| 1 | DDL, roles, views, grants | Log in as each user and try forbidden queries (`test_roles.sql`) |
| 2 | Load real data, generate synthetic sales, reconcile | Row counts, reconciliation report, **seq-scan evidence** (unindexed heavy query >3 s with full scan in `EXPLAIN`) |
| 3 | Routines, triggers, events | SQL-level tests: pricing-lock conflict, failed order leaves no partial rows, forecast sanity. Take `mysqldump` backup before this stage. |
| 4 | Backend + telemetry wrapper | Demo loop passes via API/pytest before any UI work |
| 5 | Frontend wiring (Stitch export → API) | Full demo loop in the browser with two logins |

After each stage the agent writes `docs/stage_N_report.md`, commits to git, and waits for approval. Use **Kiro in Supervised mode** (not Autopilot) for Stages 0–3; Autopilot is acceptable only for low-risk boilerplate in Stages 4–5.

## 16. Human steps (the agent must stop and ask; never fabricate)

1. Install MySQL Connector/ODBC.
2. Fill `.env` (MySQL root password, service/demo user passwords, SEC user agent, `JWT_SECRET` via `openssl rand -hex 32`).
3. Run any `SET GLOBAL` statements for `local_infile` / `event_scheduler`.
4. Provide `data/raw/segments.csv` (transcribed from filings).
5. Provide `data/raw/steam_players.csv`.
6. Provide `data/raw/events.csv` (40–60 sourced rows).
7. Google Trends manual CSV if `pytrends` is rate-limited.
8. Design screens in Stitch and place the export in `frontend/src/stitch_export/`.
9. Review every stage gate; approve or reject destructive commands (`DROP`, `TRUNCATE`, `rm -rf`).

**Stitch screens to design:** Analyst: Event Calendar, Margin Decomposition, Business Forecast, SQL Console, My Queries. Engineer: Workload Monitor, Slow-Query Queue (plan tree with highlighted bottleneck), Index Approval (before/after), Partitions, Capacity Forecast, Conflict Log. Engineer screens must never show revenue/price/cost/device values. **[OPEN: Stitch export format unverified; if it is not usable React, propose a minimal conversion without redesigning.]**

## 17. Thresholds (single values, use everywhere)

- Slow-query flag: full-scan node **> 50%** of total plan time.
- Demo speedup: rerun at least **50% faster** than the recorded baseline.
- Degradation warning: projected query time **≥ 2×** baseline under forecast load.
- Storage warning: projected 90% utilization within 30 days; UI storage indicator above 80%.
- Synthetic reconciliation: within **0.1%** per quarter per segment.

## 18. Known MySQL constraints and risks

- Partitioned InnoDB tables **cannot have foreign keys**; partition key must be part of every unique key.
- DDL auto-commits in MySQL; migrations cannot be rolled back, so the policy is halt on first error and log the failed file.
- No native row-level security (use views + mapping table); no hypothetical indexes (use INVISIBLE indexes); no `REGR_*` functions (compute regression by formula).
- Heavy-query speedup may not appear if the table is too small or data is too uniform; verify at the Stage 2 gate before building UI on it.
- Event data quality caps forecast quality; thin calendar means empty forecasts.
- Synthetic reconciliation must be done early; everything downstream depends on it.
- Stitch export quality and Kaggle/SteamCharts availability are unverified.

## 19. Rules for any agent working on this project

- No invented data. Real data comes only from the listed sources; synthetic data is only what Section 12.2 defines and is always flagged.
- Ask before any destructive command. Never run the backend as root. No passwords in code or commits.
- If a requirement conflicts with a MySQL limitation, stop and report; do not silently redesign.
- Keep ODBC (pyodbc) in `backend/`; `mysql-connector-python` only in `etl/`.
- No Oracle PL/SQL syntax in any `.sql` file (`DBMS_*`, `%TYPE`, `DECLARE … EXCEPTION`, `:=` outside MySQL `SET`).
- Do not add scope beyond this document (no byte-level JSON round-trip requirements, no responsive-width mandates, no extra reports) unless asked.
