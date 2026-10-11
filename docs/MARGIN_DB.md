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

**Unit convention (Stage 2 decision):** All financial values stored in this database are in **millions USD**. SEC EDGAR returns raw USD; `fetch_edgar.py` and `load_financials.py` must divide by 1,000,000 before inserting. `segments.csv` values are already in millions USD (header: "revenue (in million dollars)") and are inserted as-is. This applies to `QUARTERLY_FINANCIALS.revenue`, `.cogs`, `.operating_income` and `GAMING_REVENUE_SERIES.revenue_m`. Any derived or aggregated value displayed in the UI must state the unit (M USD).

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

**BUSINESS_EVENT**: `event_id PK`, `event_type` (game_launch / subscription_promo / seasonal / price_change / acquisition / outage), `name`, `start_date`, `end_date`, `expected_lift`, `steam_app_id INT NULL` (added in migration 08), `source_url NOT NULL`. Events with `steam_app_id IS NULL` (e.g. Black Friday, price changes) receive NULL lift_ratio and do not enter the Steam lift path.

**EVENT_WINDOW_MONTH** *(weak entity, owner BUSINESS_EVENT — renamed from EVENT_WINDOW_DAY in migration 08)*: **PK (event_id, month_offset)**, `avg_players DECIMAL(12,2) NULL`, `lift_ratio DECIMAL(10,4) NULL`. Month offset range: −EVENT_PRE_MONTHS..+EVENT_POST_MONTHS (both = 3 by default; see CONFIG). Offset 0 = the calendar month containing the event's start_date. lift_ratio is NULL when fewer than MIN_BASELINE_MONTHS (= 3) preceding months exist in STEAM_PLAYER; never estimated or zeroed.

**FORECAST**: `forecast_id PK`, `kind ENUM('business','database')`, `event_id FK`, `created_at`, `created_by`
**FORECAST_LINE** *(weak entity, owner FORECAST)*: **PK (forecast_id, line_no)**, `metric`, `period`, `value`

### 8.2a Gaming revenue series (separate table, not a segment)

**GAMING_REVENUE_SERIES**: `grs_id PK AI`, `fiscal_year`, `fiscal_quarter`, `revenue_m DECIMAL(12,2) NOT NULL`, `operating_income DECIMAL(12,2) NULL` (always NULL — Gaming OI not separately disclosed), `months_label VARCHAR(40) NULL` (reference metadata, e.g. "Jul 2022 to Sep 2022"), `source_url VARCHAR(500) NOT NULL`, `loaded_at DATETIME(3)`. UNIQUE KEY on `(fiscal_year, fiscal_quarter)`.

**Why a separate table:** Gaming is a product line inside the More Personal Computing segment, not a standalone reporting segment. Inserting Gaming into `QUARTERLY_FINANCIALS` would require either a spurious fourth segment or would double-count revenue against MPC. `GAMING_REVENUE_SERIES` isolates the product-line series cleanly. It is never joined into segment-level reconciliation.

**Derivation note:** "Rest of MPC" revenue (MPC total minus Gaming) is a **derived** figure, never a reported one. It must be labelled "derived" in any report or view that uses it and must never be stored as if it were sourced data.

### 8.3 Real external data tables

**QUARTERLY_FINANCIALS**: **PK (qf_id AI)**, `fiscal_year`, `fiscal_quarter`, `segment_id FK`, `revenue DECIMAL(15,2)`, `cogs DECIMAL(15,2)`, `operating_income DECIMAL(15,2) NULL`, `filed_date DATE`. Covers the three official Microsoft reporting segments: Productivity and Business Processes (PBP), Intelligent Cloud (IC), More Personal Computing (MPC). Values in **millions USD**. [OPEN: PBP/IC/MPC source — see Section 12.4 open question.]

**STEAM_PLAYER**: `sp_id BIGINT PK AI`, `app_id INT`, `obs_date DATE`, `avg_players DECIMAL(12,2) NULL` (monthly avg concurrent players; **decimal precision required**), `peak_players INT UNSIGNED NULL`. UNIQUE KEY on `(app_id, obs_date)`. Five app IDs in the dataset: 1938090 (Call of Duty), 1172620 (Sea of Thieves), 1240440 (Halo Infinite), 1551360 (Forza Horizon 5), 1716740 (Starfield).

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
4. `EVENT_WINDOW_MONTH`, identified by (event_id, month_offset), owner `BUSINESS_EVENT` *(renamed from EVENT_WINDOW_DAY in migration 08)*

Owner tables of weak entities must not be partitioned.

### 8.6 Main relationships
COMPANY_SEGMENT 1—N PRODUCT · ORDERS 1—N ORDER_ITEM · PRODUCT 1—N ORDER_ITEM · BUSINESS_EVENT 1—N EVENT_WINDOW_MONTH · BUSINESS_EVENT 1—N FORECAST · FORECAST 1—N FORECAST_LINE · QUERY_EXECUTION 1—N QUERY_PLAN_NODE · QUERY_EXECUTION 1—N INDEX_RECOMMENDATION · PRODUCT 1—N PRICING_RULE · FACT_SALES logically references PRODUCT, segment, device, region, channel, event (enforced by trigger, not FK).

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
| `sp_forecast_business(event_id)` | Applies monthly event lift to baseline; writes FORECAST + FORECAST_LINE. Reads EVENT_PRE_MONTHS, EVENT_POST_MONTHS, BASELINE_MONTHS, MIN_BASELINE_MONTHS, FORECAST_LIFT_MONTHS from CONFIG. Forecast lift = AVG(lift_ratio at offsets 0..FORECAST_LIFT_MONTHS-1) − 1; falls back to expected_lift − 1 if no non-NULL lift rows exist; falls back to 0 if neither. Records which source was used in FORECAST_LINE. |
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
| Microsoft quarterly financials (revenue, COGS, operating income) | SEC EDGAR: `https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json` | **Must send a `User-Agent` header** (name + email) or SEC rejects. Use quarterly, not cumulative/annual; take latest filed value per period for restatements. Values returned in **raw USD**; divide by 1,000,000 before inserting into QUARTERLY_FINANCIALS (which stores millions USD). |
| Gaming product-line revenue | Microsoft quarterly earnings releases, 10-Q/10-K, IR spreadsheets | **Manual transcription** into `data/raw/segments.csv`. Header: `fiscal_year, fiscal_quarter, segment, revenue (in million dollars), operating_income, source_url, Months`. Values already in millions USD; insert as-is. operating_income blank for Gaming (not separately disclosed). Months column preserved as `months_label` in GAMING_REVENUE_SERIES. |
| PBP / IC / MPC segment revenue | **[OPEN — see Section 12.4]** | EDGAR companyfacts does not reliably provide the three-segment breakdown. A second human-provided CSV is proposed. |
| Steam player counts (Call of Duty, Sea of Thieves, Halo Infinite, Forza Horizon 5, Starfield) | steam_players.csv (monthly; 5 app IDs verified via store.steampowered.com) | `data/raw/steam_players.csv`. Monthly data → month-offset lift window. avg_players is DECIMAL (not INT); store as DECIMAL(12,2). |
| Google Trends | `pytrends` for "Call of Duty", "Game Pass", "Xbox price"; manual CSV fallback | Rate-limit prone. |
| Event calendar | **Hand-curated** `data/raw/events.csv` | Columns: `event_type, name, start_date, end_date, expected_lift, steam_app_id, source_url`. Unverifiable rows go to `events_unverified_review.csv`. Every row needs `source_url`. |

### 12.2 Synthetic (explicit project decision)

| Data | Rule |
|---|---|
| Transaction-grain sales (FACT_SALES, ORDERS, ORDER_ITEM) | 2–5M rows so full scans are real. All flagged `is_synthetic=TRUE`. Seeded RNG. **Per quarter per segment, synthetic revenue must reconcile to real QUARTERLY_FINANCIALS within 0.1%** (printed report). Apply real event lifts from EVENT_WINDOW_MONTH (monthly offsets); Black Friday/holiday seasonality; device mix with OEM only on PC rows. |
| DIM_DEVICE contents (platform/OEM) | Synthetic; Microsoft does not publish device-level sales. |
| 90-day DB_SIZE_SNAPSHOT backfill | Synthetic growth curve flagged `is_synthetic`, so the regression has history; live snapshots take over. |

Everything else (query plans, timings, sizes after load) is **real measurement** from the running MySQL instance.

### 12.3 Known data limits (state honestly in the report)
- Microsoft publishes no transaction-level, per-title, per-device, discount or unit data. Margin analysis on real data is at **segment × quarter** grain.
- Event-to-margin links on real data are quarterly; finer grain exists only in the synthetic layer.
- Real Steam data covers only titles on Steam (Call of Duty since Oct 2022; Starfield since Sep 2023; Sea of Thieves, Halo Infinite, Forza Horizon 5 have earlier Steam presence but CSV data starts Oct 2022).
- "Rest of MPC" (MPC minus Gaming) is a **derived** figure — never reported by Microsoft — and must be labelled as such in every view and report that surfaces it.
- Gaming operating income is not reported; `operating_income` in GAMING_REVENUE_SERIES is always NULL. No Gaming margin is computed or displayed.

### 12.4 Open questions (require owner decision before Phase C)

**Q1 — PBP/IC/MPC revenue source:**
EDGAR companyfacts (`us-gaap.Revenues`) returns company-wide totals, not the three segment splits. Options:
- **Option A (recommended):** Owner provides a second CSV (`data/raw/segment_financials.csv`) transcribed from the segment tables in Microsoft's 10-Q/10-K filings (Section "Segment Results"). Columns: `fiscal_year, fiscal_quarter, segment_name, revenue_m, cogs_m, operating_income_m, source_url`.
- **Option B:** Load EDGAR company-wide revenue into QUARTERLY_FINANCIALS for a single "Microsoft Total" segment, and acknowledge that segment-level decomposition is unavailable from real data.

Which option do you prefer?

**Q2 — Margin / cost for the three reporting segments:**
The old design used synthetic `unit_cost` ratios to approximate segment COGS. Options:
- **Option A (recommended):** Use real segment operating income from 10-Q/10-K (sourced via Option A above). Gross margin = revenue − COGS (if COGS is disclosed at segment level; Microsoft publishes "Gross margin" per segment in its earnings releases). Label it "real sourced" in the report.
- **Option B:** Keep synthetic cost ratios per segment, clearly labelled "synthetic approximation" in every view, report, and MARGIN_DB.md. `unit_cost_ratio` on PRODUCT stays. No cost or margin for Gaming in any option.
- **Option C:** Hybrid — use real gross margin where Microsoft discloses it (PBP, IC, MPC) and synthetic cost ratios only as a fallback.

No Gaming cost or margin in any option.

**Q3 — analyst_demo and the heavy query without Gaming as a segment:**
`analyst_demo` is currently mapped to the "Gaming" segment in USER_SEGMENT_MAP. Since Gaming is no longer a COMPANY_SEGMENT row (it is in GAMING_REVENUE_SERIES), the segment filter in `v_sales` and `v_financials` will return no rows for `analyst_demo`. Options:
- **Option A:** Remap `analyst_demo` → MPC segment. The heavy demo query runs on MPC synthetic sales. Gaming revenue is visible via a separate `v_gaming_revenue` view.
- **Option B:** Keep a synthetic "Gaming" row in COMPANY_SEGMENT (segment_id only; never a row in QUARTERLY_FINANCIALS), so `analyst_demo` still scopes to Gaming-tagged FACT_SALES rows. GAMING_REVENUE_SERIES remains separate for real sourced data.
- **Option C:** Change `analyst_demo`'s persona to "MPC analyst" and update the demo script accordingly.

Which option do you prefer?

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

## 19. ETL: Q4 Derivation Method and Gaps

### Q4 derivation

Microsoft does not always file a standalone 10-Q for Q4 because its annual 10-K implicitly covers the full fiscal year. `etl/fetch_edgar.py` handles this as follows:

1. **Annual anchor**: For each fiscal year, the entry with `fp="FY"` or `form="10-K"` (and the latest `filed` date, to handle restatements) provides the full-year value.
2. **Derivation formula**: `Q4 = FY_annual_value − (Q1_value + Q2_value + Q3_value)`, where Q1–Q3 values are the latest-filed quarterly frames for that fiscal year.
3. **Derived rows** are tagged internally (`"derived": True`) but appear in the output with `filed_date` set to the annual filing's filed date.
4. **Deduplication**: For every `(fiscal_year, fp)` pair, only the entry with the latest `filed` date is kept; this ensures that restated filings replace earlier versions.

### Known gaps

- If the FY annual entry is absent for a given metric and fiscal year, Q4 cannot be derived. The script logs a warning and prints the gap to stdout.
- If any of Q1, Q2, or Q3 is missing for a fiscal year, Q4 derivation is skipped for that year and metric; the gap is printed to stdout.
- Derived Q4 values may be negative for metrics where the annual figure was revised downward relative to the quarterly sum (e.g., reclassification restatements). These are kept as-is; the loader should validate them.

### Proxy assumption (Steam lift — monthly)

`sp_forecast_business` uses Steam player-count lift (from `EVENT_WINDOW_MONTH.lift_ratio`) as a proxy for gaming revenue lift during a game-launch event. The assumption is: sustained higher concurrent player counts on Steam correlate with higher digital-sales and Game Pass conversion during the launch window. This proxy is an approximation; actual revenue response may differ due to physical-vs-digital mix, regional pricing, and platform exclusivity periods.

**Monthly offset logic (replacing former day-offset logic):**
- Offset 0 = the calendar month containing the event's `start_date`.
- Stored window = −EVENT_PRE_MONTHS (= 3) to +EVENT_POST_MONTHS (= 3).
- Baseline = mean of the BASELINE_MONTHS (= 3) months immediately before offset 0 (offsets −3, −2, −1). Computed only when all MIN_BASELINE_MONTHS (= 3) of those months are present in STEAM_PLAYER; otherwise lift_ratio = NULL.
- When fewer than MIN_BASELINE_MONTHS baseline months exist, the missing months are logged to stdout AND to a log file under `data/clean/`. Missing months are never treated as zero and are never interpolated.
- Forecast lift = AVG(non-NULL lift_ratio at offsets 0..FORECAST_LIFT_MONTHS-1, i.e. 0..+2) − 1. Falls back to `expected_lift − 1` if no measured lift exists. Falls back to 0 if neither. FORECAST_LINE records which source was used.

All five CONFIG keys (EVENT_PRE_MONTHS, EVENT_POST_MONTHS, BASELINE_MONTHS, MIN_BASELINE_MONTHS, FORECAST_LIFT_MONTHS) are stored in the CONFIG table. All code reads them from CONFIG; they are never hardcoded.

---

## 20. Rules for any agent working on this project

- No invented data. Real data comes only from the listed sources; synthetic data is only what Section 12.2 defines and is always flagged.
- Ask before any destructive command. Never run the backend as root. No passwords in code or commits.
- If a requirement conflicts with a MySQL limitation, stop and report; do not silently redesign.
- Keep ODBC (pyodbc) in `backend/`; `mysql-connector-python` only in `etl/`.
- No Oracle PL/SQL syntax in any `.sql` file (`DBMS_*`, `%TYPE`, `DECLARE … EXCEPTION`, `:=` outside MySQL `SET`).
- Do not add scope beyond this document (no byte-level JSON round-trip requirements, no responsive-width mandates, no extra reports) unless asked.
