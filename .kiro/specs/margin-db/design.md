# Design Document: Margin-DB

## Overview

Margin-DB is a full-stack database-intelligence demonstration system that models Microsoft's Xbox/Gaming business unit. It ingests real SEC EDGAR quarterly financials, real Steam player-count data as a revenue-lift proxy, synthetic sales transactions at Daily_Aggregate grain, and real game-launch events. The system demonstrates:

- **Role-based access control** via MySQL roles, ODBC connect-as-user, and JWT authentication
- **Query monitoring and index recommendation** via EXPLAIN ANALYZE plan parsing, telemetry stored procedures, and a background-task approval workflow
- **Business forecasting** using trailing revenue baselines and Steam-derived event lift ratios
- **DB capacity forecasting** using linear regression over daily size snapshots
- **Partition management** via dynamic `REORGANIZE PARTITION` SQL
- **Optimistic locking** and transactional order placement for concurrency demos

### Key Design Principles

1. **ODBC-only from backend**: All backend DB calls go through pyodbc; ETL scripts use mysql-connector-python.
2. **SQL SECURITY DEFINER everywhere**: All views, procedures, and functions are defined by ADMIN_USER so they run with ADMIN_USER privileges regardless of caller.
3. **`SUBSTRING_INDEX(USER(),'@',1)` not `CURRENT_USER()`**: Used throughout all stored procedures and triggers to get the calling user's name.
4. **No plaintext passwords in source**: Passwords flow only through environment variables and the in-memory credential map.
5. **Staged development**: Six gated stages (0–5); each stage gate requires passing tests and a Stage_Report commit.
6. **MySQL 8.0 syntax only**: No Oracle PL/SQL constructs. All stored procedures use `DELIMITER`, `BEGIN...END`, `DECLARE...HANDLER`, `SIGNAL SQLSTATE`, and `PREPARE/EXECUTE/DEALLOCATE PREPARE` for dynamic SQL.

---

## Architecture

### Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        React + Vite                         │
│  /analyst/*  ──→  client.js (axios + JWT)                   │
│  /engineer/* ──→  client.js (axios + JWT)                   │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP (CORS: localhost:5173)
┌───────────────────────────▼─────────────────────────────────┐
│                      FastAPI / uvicorn                       │
│  auth.py       POST /login → JWT                            │
│  analyst.py    GET /events, GET /margin, POST /sql, ...     │
│  engineer.py   GET /slow-queries, POST /index-recs/..., ... │
│  telemetry.py  EXPLAIN ANALYZE → plan parser → background   │
│  db.py         pyodbc helpers (get_conn, call_sp, etc.)     │
└──────────┬─────────────────────────┬────────────────────────┘
           │ ODBC (analyst/engineer) │ ODBC (svc_telemetry)
┌──────────▼─────────────────────────▼────────────────────────┐
│                      MySQL 8.0 (margin_db)                   │
│  Views: v_sales, v_financials, v_events, ...                │
│  SPs: sp_recommend_index, sp_forecast_business, ...         │
│  Triggers: PRICING_RULE audit, FACT_SALES validation        │
│  Events: ev_snapshot_sizes (daily), ev_recalc_forecasts     │
└──────────────────────────────┬──────────────────────────────┘
                               │ mysql-connector-python
┌──────────────────────────────▼──────────────────────────────┐
│                      ETL Pipeline (Python)                   │
│  fetch_edgar.py, load_financials.py, load_steam.py          │
│  load_trends.py, load_events.py, compute_event_windows.py   │
│  gen_synthetic_sales.py, backfill_snapshots.py              │
│  load_sales.sh (orchestrator, 30-min timeout)               │
└─────────────────────────────────────────────────────────────┘
```

### Migration File Execution Order

| File | Purpose |
|------|---------|
| `01_ddl.sql` | All 24 tables + CONFIG + FACT_SALES partitions (CREATE TABLE IF NOT EXISTS only) |
| `02_roles.sql` | CREATE ROLE, CREATE USER (no GRANTs) |
| `03_views.sql` | All 8 views with SQL SECURITY DEFINER |
| `04_routines.sql` | All stored procedures and functions (stubs, then full) |
| `05_triggers_events.sql` | Triggers on FACT_SALES, PRICING_RULE; scheduled EVENTs |
| `06_seed_reference.sql` | Reference data for dimension tables, CONFIG initial value |
| `07_grants.sql` | ALL GRANT statements (the only file with GRANTs) |

**01_ddl.sql note**: This file uses `CREATE TABLE IF NOT EXISTS` only — no `DROP TABLE` statements. It is safe to re-run on an existing schema; it will not destroy data. For a full destructive reset, use `sql/reset_db.sql` instead (requires operator to type 'YES' at a prompt; never called by run_migrations.sh).

**envsubst note**: Password placeholders like `'${DEMO_ANALYST_PASSWORD}'` in `02_roles.sql` are substituted at runtime by `envsubst` with an explicit variable list. The `run_migrations.sh` script uses `MYSQL_PWD` to avoid exposing passwords on the process command line.

### run_migrations.sh

```bash
#!/usr/bin/env bash
set -euo pipefail

source .env

export MYSQL_PWD="$ADMIN_PASSWORD"

for file in sql/01_ddl.sql sql/02_roles.sql sql/03_views.sql \
            sql/04_routines.sql sql/05_triggers_events.sql \
            sql/06_seed_reference.sql sql/07_grants.sql; do
  echo "Applying $file ..."
  envsubst '${ADMIN_PASSWORD} ${DEMO_ANALYST_PASSWORD} ${DEMO_ANALYST_ALL_PASSWORD} ${DEMO_ENGINEER_PASSWORD} ${SVC_TELEMETRY_PASSWORD}' < "$file" \
    | mysql -h "$DB_HOST" -P "$DB_PORT" -u "$ADMIN_USER" "$DB_NAME" \
    || { echo "ERROR: $file failed. Halting migration." >&2; exit 1; }
  echo "  OK: $file"
done
echo "All migrations applied successfully."
```

### Stage 0 HUMAN STEP — Create database and ADMIN_USER

Before any code is written, the operator must log in to MySQL as root and execute:

```sql
CREATE DATABASE IF NOT EXISTS margin_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'admin_user'@'localhost' IDENTIFIED BY '<password>';
GRANT ALL ON margin_db.* TO 'admin_user'@'localhost' WITH GRANT OPTION;
GRANT CREATE USER, CREATE ROLE, ROLE_ADMIN ON *.* TO 'admin_user'@'localhost';
GRANT SELECT ON performance_schema.* TO 'admin_user'@'localhost' WITH GRANT OPTION;
SET GLOBAL event_scheduler = ON;
SET GLOBAL local_infile = 1;
```

Then update `.env`: `ADMIN_USER=admin_user`, `ADMIN_PASSWORD=<chosen password>`, `DB_NAME=margin_db`. This step must complete before the Stage 0 gate.

---

## Components and Interfaces

### Backend Module Interfaces

| Module | Responsibility |
|--------|---------------|
| `main.py` | App factory, startup checks, CORS, router registration |
| `auth.py` | POST /login, JWT issue/decode, credential map |
| `db.py` | pyodbc helpers: `get_connection`, `call_sp`, `call_sp_out` |
| `telemetry.py` | EXPLAIN ANALYZE parser, telemetry write, background index task |
| `routers/analyst.py` | All /analyst/* endpoints |
| `routers/engineer.py` | All /engineer/* endpoints |
| `models/requests.py` | Pydantic request models |
| `models/responses.py` | Pydantic response models |

### ODBC Connection Pattern

```python
# db.py

def get_connection(username: str, password: str, role: str) -> pyodbc.Connection:
    conn_str = (
        f"DRIVER={{MySQL ODBC 8.0 Unicode Driver}};"
        f"SERVER={DB_HOST};PORT={DB_PORT};DATABASE={DB_NAME};"
        f"UID={username};PWD={password};charset=utf8mb4"
    )
    conn = pyodbc.connect(conn_str, timeout=10)
    cursor = conn.cursor()
    # Set MAX_EXECUTION_TIME=30000 on analyst sessions
    if role == "business_analyst":
        cursor.execute("SET SESSION MAX_EXECUTION_TIME=30000")
    cursor.close()
    return conn

def call_sp(conn, sp_name: str, params: list):
    """Call a stored procedure that returns a result set (or no result set)."""
    placeholders = ", ".join("?" * len(params))
    cursor = conn.cursor()
    cursor.execute(f"{{CALL {sp_name}({placeholders})}}", params)
    rows = cursor.fetchall() if cursor.description is not None else []
    # Drain any trailing result sets to avoid protocol errors
    while cursor.nextset():
        pass
    cursor.close()
    conn.commit()   # commit after any write SP
    return rows

def call_sp_out(conn, sp_name: str, in_params: list, out_count: int):
    """Call a stored procedure with OUT parameters.

    The CALL statement uses positional ? for in-params and named @out0..@outN for
    out-params. Example: {CALL sp_insert_qe(?, ?, ?, ?, ?, ?, @out0)}
    Bind in-params positionally; reference out-params as @out0 etc.
    After the CALL, drain intermediate result sets with nextset() before
    executing SELECT @out0, ... to read the OUT values.
    """
    in_placeholders = ", ".join("?" * len(in_params))
    out_placeholders = ", ".join(f"@out{i}" for i in range(out_count))
    all_placeholders = f"{in_placeholders}, {out_placeholders}" if in_params else out_placeholders
    cursor = conn.cursor()
    cursor.execute(
        f"{{CALL {sp_name}({all_placeholders})}}",
        in_params
    )
    # Drain all intermediate result sets
    while cursor.nextset():
        pass
    # Read OUT parameters
    out_vars = ", ".join(f"@out{i}" for i in range(out_count))
    cursor.execute(f"SELECT {out_vars}")
    out_row = cursor.fetchone()
    cursor.close()
    conn.commit()
    return out_row
```

**Three rules for stored procedure calls in db.py:**

1. **After calling a write stored procedure**, call `conn.commit()` to ensure the write is visible to other connections.
2. **For procedures with OUT parameters**: call `cursor.nextset()` in a loop until `nextset()` returns `False` or `None` to drain any intermediate result sets before executing `SELECT @out0, @out1, ...`. The CALL statement format is `{CALL sp_name(?, ?, @out0)}` — bind in-params with `?`, reference out-params as `@out0` etc.
3. **For procedures with no OUT parameters**: after `fetchall()`, drain trailing result sets with a `while cursor.nextset(): pass` loop before closing the cursor. If a stored procedure returns no result set, `cursor.description` will be `None`; handle this without raising an exception.

---

## Data Models

### 24 Tables

#### Core Dimension Tables

| Table | Key Columns | Notes |
|-------|-------------|-------|
| `COMPANY_SEGMENT` | `segment_id INT PK AUTO_INCREMENT`, `name VARCHAR(50) UNIQUE NOT NULL` | Three official reporting segments: Productivity and Business Processes (PBP), Intelligent Cloud (IC), More Personal Computing (MPC). Gaming is a product line inside MPC — it is NOT a COMPANY_SEGMENT row. |
| `DIM_DATE` | `date_id INT PK`, `calendar_date DATE UNIQUE`, `fiscal_year SMALLINT`, `fiscal_quarter TINYINT`, `is_black_friday BOOLEAN`, `is_holiday BOOLEAN` | 2022-07-01 to 2027-06-30; Microsoft FY ends June 30 |
| `DIM_DEVICE` | `device_id INT PK AUTO_INCREMENT`, `platform VARCHAR(20)`, `device_family VARCHAR(50)`, `oem VARCHAR(50) NULL` | oem non-null only for Windows PC |
| `DIM_REGION` | `region_id INT PK AUTO_INCREMENT`, `name VARCHAR(50)`, `parent_region VARCHAR(50)` | Americas, EMEA, APAC |
| `DIM_CHANNEL` | `channel_id INT PK AUTO_INCREMENT`, `name VARCHAR(50)` | Digital, Physical, Subscription |
| `PRODUCT` | `product_id INT PK AUTO_INCREMENT`, `segment_id INT FK→COMPANY_SEGMENT`, `name VARCHAR(100)`, `sku VARCHAR(20)`, `unit_cost_ratio DECIMAL(5,4)` | At least 100 products total, distributed across PBP, IC, MPC segments |
| `USER_SEGMENT_MAP` | `db_user VARCHAR(64)`, `segment_id INT FK→COMPANY_SEGMENT`; PK(db_user, segment_id) | analyst_demo→MPC (pending owner decision on Q3 from MARGIN_DB.md §12.4); analyst_all→all |
| `CONFIG` | `config_key VARCHAR(64) PK`, `config_value VARCHAR(255) NOT NULL` | Runtime key-value store. Keys seeded: DB_ALLOCATED_MB, AT_RISK_FACTOR, EVENT_PRE_MONTHS=3, EVENT_POST_MONTHS=3, BASELINE_MONTHS=3, MIN_BASELINE_MONTHS=3, FORECAST_LIFT_MONTHS=3. All lift/forecast code reads these from CONFIG; never hardcode. |

#### Sales and Financial Tables

| Table | Key Columns | Notes |
|-------|-------------|-------|
| `FACT_SALES` | `sale_id BIGINT AUTO_INCREMENT`, `sale_date DATE`, `product_id INT`, `segment_id INT`, `device_id INT`, `region_id INT`, `channel_id INT`, `qty INT`, `gross_amount DECIMAL(15,2)`, `discount_amount DECIMAL(15,2)`, `cost_amount DECIMAL(15,2)`, `is_synthetic BOOLEAN NOT NULL DEFAULT TRUE` | PK(sale_id, sale_date); PARTITION BY RANGE(TO_DAYS(sale_date)); NO FK constraints; 20 named partitions + p_catch |
| `QUARTERLY_FINANCIALS` | `qf_id INT PK AUTO_INCREMENT`, `fiscal_year SMALLINT`, `fiscal_quarter TINYINT`, `segment_id INT FK→COMPANY_SEGMENT`, `revenue DECIMAL(15,2)`, `cogs DECIMAL(15,2)`, `operating_income DECIMAL(15,2)`, `filed_date DATE` | Deduped by latest filed_date per (period, metric) |
| `ORDERS` | `order_id INT PK AUTO_INCREMENT`, `order_date DATE`, `device_id INT FK→DIM_DEVICE`, `region_id INT FK→DIM_REGION`, `channel_id INT FK→DIM_CHANNEL`, `status VARCHAR(20)` | |
| `ORDER_ITEM` | `order_id INT FK→ORDERS`, `line_no INT`; PK(order_id, line_no) | Weak entity; FK→ORDERS |
| `PRICING_RULE` | `rule_id INT PK AUTO_INCREMENT`, `product_id INT FK→PRODUCT`, `effective_date DATE`, `price_value DECIMAL(10,4)`, `version INT NOT NULL DEFAULT 1` | Optimistic lock version |
| `AUDIT_LOG` | `log_id BIGINT PK AUTO_INCREMENT`, `table_name VARCHAR(64)`, `operation VARCHAR(10)`, `old_value JSON`, `new_value JSON`, `db_user VARCHAR(64)`, `at DATETIME(3)` | Populated by PRICING_RULE triggers |
| `CONFLICT_LOG` | `conflict_id BIGINT PK AUTO_INCREMENT`, `rule_id INT`, `attempted_by VARCHAR(64)`, `stale_version INT`, `current_version INT`, `at DATETIME(3)` | |

#### Market Data Tables

| Table | Key Columns | Notes |
|-------|-------------|-------|
| `BUSINESS_EVENT` | `event_id INT PK AUTO_INCREMENT`, `event_type VARCHAR(30)`, `name VARCHAR(200)`, `start_date DATE`, `end_date DATE`, `expected_lift DECIMAL(5,4)`, `steam_app_id INT NULL`, `source_url VARCHAR(500)` | steam_app_id links to STEAM_PLAYER.app_id for game-launch events; NULL for non-Steam events (Black Friday, price changes, etc.) — events with NULL steam_app_id get NULL lift_ratio and bypass the Steam path entirely |
| `STEAM_PLAYER` | `sp_id BIGINT PK AUTO_INCREMENT`, `app_id INT`, `obs_date DATE`, `avg_players DECIMAL(12,2) NULL`, `peak_players INT UNSIGNED` | UNIQUE(app_id, obs_date); avg_players is DECIMAL — source CSV has fractional values (e.g. 103689.35); INT UNSIGNED would silently truncate |
| `EVENT_WINDOW_MONTH` | `event_id INT FK→BUSINESS_EVENT`, `month_offset SMALLINT`; PK(event_id, month_offset) | Weak entity; offsets −EVENT_PRE_MONTHS..+EVENT_POST_MONTHS (default −3..+3); `avg_players DECIMAL(12,2) NULL`; `lift_ratio DECIMAL(10,4) NULL`. Offset 0 = calendar month of event start_date. lift_ratio = NULL when fewer than MIN_BASELINE_MONTHS (3) baseline months exist; never zero, never interpolated. |
| `GOOGLE_TREND` | `gt_id INT PK AUTO_INCREMENT`, `keyword VARCHAR(200)`, `obs_date DATE`, `interest_score TINYINT UNSIGNED` | |
| `GAMING_REVENUE_SERIES` | `grs_id INT PK AUTO_INCREMENT`, `fiscal_year SMALLINT NOT NULL`, `fiscal_quarter TINYINT NOT NULL`, `revenue_m DECIMAL(12,2) NOT NULL`, `operating_income DECIMAL(12,2) NULL`, `months_label VARCHAR(40) NULL`, `source_url VARCHAR(500) NOT NULL`, `loaded_at DATETIME(3)` | UNIQUE KEY (fiscal_year, fiscal_quarter). Source: segments.csv. Values in millions USD. operating_income always NULL (Gaming OI not separately disclosed — never estimated). months_label preserves the "Months" reference column from segments.csv. NOT a COMPANY_SEGMENT row. Gaming sits inside MPC; this table is separate to avoid double-counting. |

#### Intelligence and Monitoring Tables

| Table | Key Columns | Notes |
|-------|-------------|-------|
| `QUERY_EXECUTION` | `qe_id BIGINT PK AUTO_INCREMENT`, `db_user VARCHAR(64)`, `digest_text TEXT`, `started_at DATETIME(3)`, `duration_ms DECIMAL(12,3)`, `rows_examined INT UNSIGNED`, `rows_sent INT UNSIGNED`, `flagged TINYINT(1) DEFAULT 0` | Written by svc_telemetry via sp_insert_qe |
| `QUERY_PLAN_NODE` | `qe_id BIGINT FK→QUERY_EXECUTION`, `node_id SMALLINT`; PK(qe_id, node_id) | Weak entity; `parent_node_id SMALLINT NULL`, `operation VARCHAR(200)`, `table_name VARCHAR(64)`, `pct_time DECIMAL(6,2)`, `est_rows BIGINT`, `actual_rows BIGINT` |
| `INDEX_RECOMMENDATION` | `rec_id INT PK AUTO_INCREMENT`, `qe_id BIGINT FK→QUERY_EXECUTION`, `table_name VARCHAR(64)`, `columns_csv VARCHAR(500)`, `index_name VARCHAR(128)`, `status VARCHAR(20) DEFAULT 'proposed'`, `before_ms DECIMAL(12,3) NULL`, `after_ms DECIMAL(12,3) NULL`, `decided_by VARCHAR(64) NULL`, `decided_at DATETIME(3) NULL` | |
| `DB_SIZE_SNAPSHOT` | `snap_id INT PK AUTO_INCREMENT`, `snapped_at DATE UNIQUE`, `total_data_mb DECIMAL(12,2)`, `total_index_mb DECIMAL(12,2)`, `is_synthetic BOOLEAN NOT NULL DEFAULT FALSE` | UNIQUE KEY on snapped_at to prevent duplicate daily snapshots |
| `FORECAST` | `forecast_id INT PK AUTO_INCREMENT`, `kind VARCHAR(20)`, `event_id INT NULL FK→BUSINESS_EVENT`, `as_of_date DATE`, `created_at DATETIME(3)` | kind: 'business' or 'database' |
| `FORECAST_LINE` | `forecast_id INT FK→FORECAST`, `line_no INT`; PK(forecast_id, line_no) | Weak entity; `metric_name VARCHAR(100)`, `metric_value DECIMAL(20,4)`, `error_indicator VARCHAR(200) NULL` |

### Views

| View | Source | Filter | Notes |
|------|--------|--------|-------|
| `v_sales` | FACT_SALES + PRODUCT + DIM_DATE + DIM_DEVICE + DIM_REGION + DIM_CHANNEL | Segment filter via USER_SEGMENT_MAP | Exposes platform, device_family, oem, region_name (DIM_REGION.name), channel_name (DIM_CHANNEL.name); FACT_SALES has NO alias to keep EXPLAIN ANALYZE output unambiguous |
| `v_financials` | QUARTERLY_FINANCIALS JOIN COMPANY_SEGMENT | Segment filter via USER_SEGMENT_MAP | |
| `v_events` | BUSINESS_EVENT | No filter | Full event details including source_url and expected_lift |
| `v_forecast_business` | FORECAST JOIN FORECAST_LINE WHERE kind='business' | No filter | |
| `v_my_queries` | QUERY_EXECUTION LEFT JOIN INDEX_RECOMMENDATION | Filtered to `db_user = SUBSTRING_INDEX(USER(),'@',1)` — this is a per-user filter, NOT a segment filter | Returns query history for the calling user only |
| `v_events_min` | BUSINESS_EVENT | No filter | Only: name, start_date, end_date, expected_lift — NO revenue columns |
| `v_forecast_database` | FORECAST JOIN FORECAST_LINE WHERE kind='database' | No filter | |
| `v_partitions` | information_schema.PARTITIONS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='FACT_SALES' | No filter | |

All views: `DROP VIEW IF EXISTS` + `CREATE VIEW ... SQL SECURITY DEFINER AS ...`

Segment filter pattern used in v_sales, v_financials:
```sql
WHERE segment_id IN (
  SELECT segment_id FROM USER_SEGMENT_MAP
  WHERE db_user = SUBSTRING_INDEX(USER(),'@',1)
)
```

### Index Strategy

- `FACT_SALES`: no secondary indexes at schema creation; indexes are added via the index recommendation workflow during the demo
- `QUERY_EXECUTION`: index on `(flagged, started_at DESC)` for slow-query queue queries
- `INDEX_RECOMMENDATION`: index on `(qe_id)`, index on `(table_name, columns_csv, status)` for deduplication check
- `DB_SIZE_SNAPSHOT`: index on `snapped_at DESC` for linear regression window

---

## Stored Procedure Designs

All procedures: `SQL SECURITY DEFINER`, owned by `ADMIN_USER`. All use `SUBSTRING_INDEX(USER(),'@',1)` not `CURRENT_USER()`. No Oracle PL/SQL syntax.

### sp_margin_decomposition

```
Signature: sp_margin_decomposition(
  IN p_segment VARCHAR(50),
  IN p_period_a VARCHAR(10),
  IN p_period_b VARCHAR(10)
)
Optional session variable: @group_by (controls additional output grouping if set)

Logic:
  0. Intersect: verify caller has access to the requested segment:
       WHERE segment_id IN (
         SELECT segment_id FROM USER_SEGMENT_MAP
         WHERE db_user = SUBSTRING_INDEX(USER(),'@',1)
       ) AND segment_name = p_segment
     If no matching rows: RETURN empty result set immediately.
  1. SELECT SUM(gross_amount), SUM(discount_amount), SUM(cost_amount), SUM(qty)
     FROM FACT_SALES JOIN DIM_DATE WHERE fiscal_year/quarter matches p_period_a → vars_a
  2. Same for p_period_b → vars_b
  3. Compute: volume_effect = (qty_b - qty_a) × (margin_per_unit_a)
              price_effect  = qty_b × (avg_price_b - avg_price_a)
              discount_effect = qty_b × (avg_discount_b - avg_discount_a)
              mix_effect    = remainder
  4. Return SELECT 'volume', volume_effect
            UNION SELECT 'price', price_effect
            UNION SELECT 'discount', discount_effect
            UNION SELECT 'mix', mix_effect
```

### sp_forecast_business

```
Signature: sp_forecast_business(IN p_event_id INT UNSIGNED)
Session variable: @as_of_date (NULL → CURDATE())

Logic:
  0. Read CONFIG keys (all code uses CONFIG, never hardcoded values):
       EVENT_PRE_MONTHS, EVENT_POST_MONTHS, BASELINE_MONTHS,
       MIN_BASELINE_MONTHS, FORECAST_LIFT_MONTHS

  1. baseline_monthly_revenue = AVG monthly revenue from QUARTERLY_FINANCIALS
     for the trailing 4 quarters before @as_of_date
     (across PBP/IC/MPC as applicable to the caller's USER_SEGMENT_MAP)

  2. event_duration_months = PERIOD_DIFF(
       DATE_FORMAT(end_date, '%Y%m'),
       DATE_FORMAT(start_date, '%Y%m')
     ) + 1 from BUSINESS_EVENT

  2a. Verify the event's segment is in the caller's USER_SEGMENT_MAP entries
      using the same pattern as sp_margin_decomposition;
      if not permitted, return empty result set.

  3. Lift source selection (monthly, CONFIG-driven):
     a. IF BUSINESS_EVENT.steam_app_id IS NOT NULL
        AND EVENT_WINDOW_MONTH rows exist for p_event_id
        AND at least one lift_ratio IS NOT NULL at offsets
            0..(FORECAST_LIFT_MONTHS - 1)  (i.e. offsets 0, 1, 2):
          measured_lift = AVG(lift_ratio at those offsets WHERE lift_ratio IS NOT NULL)
          event_lift    = measured_lift - 1
          lift_source   = 'measured'

          (Steam monthly player-count lift as proxy for revenue lift;
           proxy assumption documented in docs/MARGIN_DB.md §19.
           Baseline for lift_ratio = mean avg_players of the BASELINE_MONTHS
           months immediately before offset 0; NULL if < MIN_BASELINE_MONTHS
           baseline months exist — see compute_event_windows.py.)

     b. ELSE IF BUSINESS_EVENT.expected_lift IS NOT NULL:
          event_lift  = expected_lift - 1
          lift_source = 'expected_lift'

     c. ELSE:
          event_lift  = 0
          lift_source = 'zero_fallback'

  4. projected_revenue_impact =
       baseline_monthly_revenue × event_duration_months × event_lift

  5. confidence_tier:
       high   = all three inputs present (baseline, lift from window, event dates)
       medium = two inputs present
       low    = 0 or 1 inputs present

  6. Write FORECAST (kind='business', event_id, as_of_date)
     Write FORECAST_LINE rows: baseline_monthly_revenue, event_lift,
       event_duration_months, projected_revenue_impact, confidence_tier,
       lift_source (records which of 'measured'/'expected_lift'/'zero_fallback'
       was used — required for auditability)

  7. SELECT from FORECAST JOIN FORECAST_LINE for this forecast_id

  NOTE: No Gaming-specific segment references. No Gaming cost, margin, or
  operating income anywhere in this procedure.
```

### sp_forecast_database

```
Signature: sp_forecast_database(IN p_event_id INT UNSIGNED)
Session variable: @as_of_date (NULL → CURDATE())

Logic:
  1. Read DB_ALLOCATED_MB from CONFIG WHERE config_key = 'DB_ALLOCATED_MB'
     IF not found: SET v_db_mb = @db_allocated_mb (session fallback)
  2. Read AT_RISK_FACTOR from CONFIG WHERE config_key = 'AT_RISK_FACTOR'
     IF not found: SET v_at_risk_factor = 1.25
  3. SELECT snapped_at, (total_data_mb + total_index_mb) AS total_mb
     FROM DB_SIZE_SNAPSHOT WHERE snapped_at >= DATE_SUB(@as_of_date, INTERVAL 90 DAY)
     ORDER BY snapped_at
  4. Linear regression: slope_mb_per_day = (N*Σxy - Σx*Σy) / (N*Σx² - (Σx)²)
     where x = days_since_earliest, y = total_mb
  5. GUARD: IF slope_mb_per_day <= 0:
       SET days_to_90 = NULL
       Write FORECAST_LINE with error_indicator = 'slope_non_positive'
       RETURN (do not raise exception)
  6. current_mb = latest total_mb from snapshot
     days_to_90 = (v_db_mb * 0.9 - current_mb) / slope_mb_per_day
  7. For event p_event_id:
       Get event_lift (same lookup pattern as sp_forecast_business)
       projected_rows = current_rows + rows_per_day × event_duration_days × (1 + event_lift)
       projected_growth_mb = slope_mb_per_day × (1 + event_lift) × event_duration_days
       at_risk queries: projected_duration_ms = baseline_ms × (projected_rows / current_rows)
       mark at_risk = TRUE if projected_duration_ms >= v_at_risk_factor × baseline_ms
       ordered by event date
  8. Write FORECAST (kind='database') + FORECAST_LINE rows:
       days_to_90, projected_growth_mb, at_risk_query list, error_indicator if slope guard fired
  9. 30-day warning: if days_to_90 < 30, add FORECAST_LINE with metric_name='warning_30_day'
```

### sp_update_pricing_rule

```
Signature: sp_update_pricing_rule(
  IN p_rule_id INT,
  IN p_new_value DECIMAL(10,4),
  IN p_expected_version INT,
  OUT p_status VARCHAR(20)
)
Logic:
  1. UPDATE PRICING_RULE
     SET price_value = p_new_value, version = version + 1
     WHERE rule_id = p_rule_id AND version = p_expected_version
  2. IF ROW_COUNT() = 1:
       SET p_status = 'OK'
  3. ELSE:
       INSERT INTO CONFLICT_LOG (rule_id, attempted_by,
         stale_version, current_version, at)
       VALUES (p_rule_id, SUBSTRING_INDEX(USER(),'@',1),
         p_expected_version,
         (SELECT version FROM PRICING_RULE WHERE rule_id=p_rule_id),
         NOW(3))
       SET p_status = 'CONFLICT'
       -- NO SIGNAL; caller reads p_status
```

### sp_place_order

```
Signature: sp_place_order(
  IN p_order_date DATE,
  IN p_device_id INT,
  IN p_region_id INT,
  IN p_channel_id INT,
  IN p_items JSON
)
Logic:
```

```sql
DECLARE v_order_id INT;

DECLARE EXIT HANDLER FOR SQLEXCEPTION
BEGIN
  ROLLBACK;
  RESIGNAL;
END;

START TRANSACTION;

SAVEPOINT sp_orders;
INSERT INTO ORDERS (order_date, device_id, region_id, channel_id, status)
  VALUES (p_order_date, p_device_id, p_region_id, p_channel_id, 'placed');
SET v_order_id = LAST_INSERT_ID();

-- Use JSON_TABLE to iterate items
INSERT INTO ORDER_ITEM (order_id, line_no, product_id, qty, unit_price, discount, unit_cost)
SELECT v_order_id, jt.line_no, jt.product_id, jt.qty, jt.unit_price, jt.discount, jt.unit_cost
FROM JSON_TABLE(p_items, '$[*]' COLUMNS (
  line_no     FOR ORDINALITY,
  product_id  INT PATH '$.product_id',
  qty         INT PATH '$.qty',
  unit_price  DECIMAL(10,2) PATH '$.unit_price',
  discount    DECIMAL(5,4) PATH '$.discount',
  unit_cost   DECIMAL(10,2) PATH '$.unit_cost'
)) AS jt;

SAVEPOINT sp_fact;
INSERT INTO FACT_SALES (sale_date, product_id, segment_id, device_id, region_id,
                        channel_id, qty, gross_amount, discount_amount, cost_amount, is_synthetic)
SELECT p_order_date, oi.product_id, pr.segment_id, p_device_id, p_region_id,
       p_channel_id, SUM(oi.qty), SUM(oi.qty * oi.unit_price),
       SUM(oi.qty * oi.unit_price * oi.discount), SUM(oi.qty * oi.unit_cost), TRUE
FROM ORDER_ITEM oi JOIN PRODUCT pr ON oi.product_id = pr.product_id
WHERE oi.order_id = v_order_id
GROUP BY oi.product_id, pr.segment_id;

COMMIT;
```

### sp_recommend_index

```
Signature: sp_recommend_index(
  IN p_qe_id BIGINT,
  IN p_table VARCHAR(64),
  IN p_columns_csv VARCHAR(500),
  OUT p_rec_id INT
)
Logic:
  Step 1a. Deduplicate: check INDEX_RECOMMENDATION for existing active rec
           WHERE table_name = p_table AND columns_csv = p_columns_csv
           AND status IN ('proposed','testing','applied')
           IF found: RETURN (no-op)
  Step 1b. Whitelist check: verify p_table is in allowed list (e.g., FACT_SALES)
           IF NOT: SIGNAL SQLSTATE '45001' SET MESSAGE_TEXT = 'Table not whitelisted'
  Step 1c. Column validation: for each column in p_columns_csv (split on ','):
           - must match ^\w+$ (checked via REGEXP)
           - must exist in information_schema.COLUMNS WHERE
             TABLE_SCHEMA=DATABASE() AND TABLE_NAME=p_table AND COLUMN_NAME=trimmed_col
           IF any column invalid: SIGNAL SQLSTATE '45002' SET MESSAGE_TEXT = 'Invalid column'
  Step 2.  INSERT INTO INDEX_RECOMMENDATION
           (qe_id, table_name, columns_csv, index_name, status)
           VALUES (p_qe_id, p_table, p_columns_csv, 'placeholder', 'testing')
  Step 3.  SET v_rec_id = LAST_INSERT_ID()
           UPDATE INDEX_RECOMMENDATION
           SET index_name = CONCAT('idx_rec_', v_rec_id)
           WHERE rec_id = v_rec_id
           SET p_rec_id = v_rec_id
  Step 4.  SET @sql = CONCAT(
             'ALTER TABLE ', p_table,
             ' ADD INDEX idx_rec_', v_rec_id,
             ' (', p_columns_csv, ') INVISIBLE, ALGORITHM=INPLACE'
           )
           PREPARE stmt FROM @sql;
           EXECUTE stmt;
           DEALLOCATE PREPARE stmt;
```

Note: The index is created as INVISIBLE so it does not affect query plans until the engineer explicitly approves it. The syntax is `ADD INDEX name (cols) INVISIBLE, ALGORITHM=INPLACE` — INVISIBLE comes before the comma and ALGORITHM clause.

### sp_record_index_test

```
Signature: sp_record_index_test(
  IN p_rec_id INT,
  IN p_before_ms DECIMAL(12,3),
  IN p_after_ms DECIMAL(12,3)
)
Logic:
  UPDATE INDEX_RECOMMENDATION
  SET before_ms = p_before_ms, after_ms = p_after_ms
  WHERE rec_id = p_rec_id
```

### sp_flag_query

```
Signature: sp_flag_query(IN p_qe_id BIGINT)
Logic:
  UPDATE QUERY_EXECUTION SET flagged = 1 WHERE qe_id = p_qe_id;
SQL SECURITY DEFINER, owned by ADMIN_USER.
Called by telemetry.py via the svc_telemetry connection when the flag condition is met.
role_telemetry has EXECUTE on this procedure.
```

### sp_apply_index

```
Signature: sp_apply_index(IN p_rec_id INT)
Logic:
  1. SELECT index_name, table_name, status, after_ms FROM INDEX_RECOMMENDATION WHERE rec_id = p_rec_id
  2. IF after_ms IS NULL:
       Return error result set: SELECT 'Index test not yet complete' AS result
       (caller maps this to HTTP 409; do NOT proceed)
  3. IF status != 'testing': SIGNAL SQLSTATE '45003' SET MESSAGE_TEXT = 'Not in testing status'
  4. SET @sql = CONCAT('ALTER TABLE ', table_name, ' ALTER INDEX ', index_name, ' VISIBLE')
     PREPARE/EXECUTE/DEALLOCATE
  5. UPDATE INDEX_RECOMMENDATION
     SET status = 'applied',
         decided_by = SUBSTRING_INDEX(USER(),'@',1),
         decided_at = NOW(3)
     WHERE rec_id = p_rec_id
  6. SELECT 'applied' AS result
```

### sp_reject_index

```
Signature: sp_reject_index(IN p_rec_id INT)
Logic:
  1. SELECT index_name, table_name, status FROM INDEX_RECOMMENDATION WHERE rec_id = p_rec_id
  2. IF status NOT IN ('testing','proposed'): SIGNAL
  3. SET @sql = CONCAT('ALTER TABLE ', table_name, ' DROP INDEX ', index_name)
     PREPARE/EXECUTE/DEALLOCATE
     (If ALTER fails due to index not existing, catch and return error result set — do not re-raise)
  4. UPDATE INDEX_RECOMMENDATION
     SET status = 'rejected',
         decided_by = SUBSTRING_INDEX(USER(),'@',1),
         decided_at = NOW(3)
     WHERE rec_id = p_rec_id
  5. SELECT 'rejected' AS result
```

### sp_add_partition

```
Signature: sp_add_partition(IN p_table_name VARCHAR(64), IN p_boundary DATE)
Logic:
  1. Whitelist p_table_name (only FACT_SALES allowed); SIGNAL if not
  2. Generate partition name from boundary: p_FYyyQq format
  3. SET @sql = CONCAT(
       'ALTER TABLE ', p_table_name,
       ' REORGANIZE PARTITION p_catch INTO (',
       'PARTITION ', partition_name, ' VALUES LESS THAN (TO_DAYS(''', p_boundary, ''')),',
       'PARTITION p_catch VALUES LESS THAN MAXVALUE)'
     )
     PREPARE/EXECUTE/DEALLOCATE
```

### sp_snapshot_sizes

```
Signature: sp_snapshot_sizes()
Logic:
  1. SET SESSION information_schema_stats_expiry = 0
     (forces immediate refresh of table statistics)
  2. INSERT INTO DB_SIZE_SNAPSHOT (snapped_at, total_data_mb, total_index_mb, is_synthetic)
     SELECT CURDATE(),
            ROUND(SUM(data_length) / 1024 / 1024, 2),
            ROUND(SUM(index_length) / 1024 / 1024, 2),
            FALSE
     FROM information_schema.TABLES
     WHERE table_schema = DATABASE()
     ON DUPLICATE KEY UPDATE
       total_data_mb = VALUES(total_data_mb),
       total_index_mb = VALUES(total_index_mb)
```

### sp_insert_qe

```
Signature: sp_insert_qe(
  IN p_db_user VARCHAR(64),
  IN p_digest_text TEXT,
  IN p_started_at DATETIME(3),
  IN p_duration_ms DECIMAL(12,3),
  IN p_rows_examined INT UNSIGNED,
  IN p_rows_sent INT UNSIGNED,
  OUT p_qe_id BIGINT UNSIGNED
)
Logic:
  INSERT INTO QUERY_EXECUTION (db_user, digest_text, started_at,
    duration_ms, rows_examined, rows_sent, flagged)
  VALUES (p_db_user, p_digest_text, p_started_at,
    p_duration_ms, p_rows_examined, p_rows_sent, 0);
  SET p_qe_id = LAST_INSERT_ID();
SQL SECURITY DEFINER, owned by ADMIN_USER.
```

### sp_insert_qpn

```
Signature: sp_insert_qpn(
  IN p_qe_id BIGINT,
  IN p_node_id SMALLINT,
  IN p_parent_node_id SMALLINT,
  IN p_operation VARCHAR(200),
  IN p_table_name VARCHAR(64),
  IN p_pct_time DECIMAL(6,2),
  IN p_est_rows BIGINT,
  IN p_actual_rows BIGINT
)
Logic:
  INSERT INTO QUERY_PLAN_NODE (qe_id, node_id, parent_node_id, operation,
    table_name, pct_time, est_rows, actual_rows)
  VALUES (p_qe_id, p_node_id, p_parent_node_id, p_operation,
    p_table_name, p_pct_time, p_est_rows, p_actual_rows);
SQL SECURITY DEFINER, owned by ADMIN_USER.
```

---

## ETL Pipeline Design

### Script Execution Sequence

```
load_sales.sh (orchestrator)
  1. fetch_edgar.py           → data/raw/msft_facts.json
  2. load_financials.py       → QUARTERLY_FINANCIALS
  [HUMAN STEP: confirm segments.csv present]
  3. load_steam.py            → STEAM_PLAYER only
  4. load_trends.py           → GOOGLE_TREND
  [HUMAN STEP: confirm events.csv present]
  5. load_events.py           → BUSINESS_EVENT
  6. compute_event_windows.py → EVENT_WINDOW_MONTH (game_launch events with non-NULL steam_app_id only; monthly offsets; after load_events.py)
  7. gen_synthetic_sales.py   → CSV chunks in data/clean/
     [HUMAN STEP: confirm drop BEFORE INSERT trigger for bulk load]
  8. LOAD DATA LOCAL INFILE loop (chunk by chunk)
     [HUMAN STEP: confirm trigger recreated after load]
  9. reconciliation check (three independent groups: PBP, IC, MPC — no Gaming group; Gaming is inside MPC)
 10. backfill_snapshots.py    → DB_SIZE_SNAPSHOT (90 synthetic rows)
 11. UPDATE CONFIG set DB_ALLOCATED_MB = computed_value
```

### fetch_edgar.py

```python
# GET https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json
# User-Agent header from SEC_USER_AGENT env var
# Require HTTP 200; non-200 → log UTC timestamp + URL + exit 1
# Deduplicate restatements: keep latest filed_date per (period, metric)
# Q4 derivation: Q4 = FY_annual − (Q1 + Q2 + Q3)
#   If FY annual is not available for a metric, log the missing quarter.
#   Document derivation method and any gaps in docs/MARGIN_DB.md.
# Print missing fiscal quarters to stdout
# Write result to data/raw/msft_facts.json
```

### compute_event_windows.py

```python
# Separate script run after load_events.py
# Only processes game_launch events WHERE steam_app_id IS NOT NULL
# Reads CONFIG keys from DB: EVENT_PRE_MONTHS, EVENT_POST_MONTHS,
#   BASELINE_MONTHS, MIN_BASELINE_MONTHS  (never hardcoded)
#
# For each qualifying BUSINESS_EVENT:
#   offset_0_month = calendar month of start_date (YYYY-MM)
#   window_months  = offset_0_month - EVENT_PRE_MONTHS .. offset_0_month + EVENT_POST_MONTHS
#                    (i.e. offsets -3 .. +3 with default CONFIG values)
#
#   baseline_months = the BASELINE_MONTHS (3) calendar months immediately before offset 0
#                     i.e. offsets -BASELINE_MONTHS .. -1  (offsets -3, -2, -1)
#   IF all MIN_BASELINE_MONTHS (3) baseline months exist in STEAM_PLAYER for this app_id:
#     baseline_avg = AVG(avg_players for those months)
#   ELSE:
#     baseline_avg = NULL
#     log missing months to stdout AND to data/clean/lift_warnings_<event_id>.log
#
#   For each month_offset in window:
#     target_month = offset_0_month + month_offset
#     avg_players  = STEAM_PLAYER.avg_players WHERE app_id=steam_app_id
#                    AND obs_date = first day of target_month
#                    (NULL if no row; never treated as 0, never interpolated)
#     IF baseline_avg IS NOT NULL AND avg_players IS NOT NULL:
#       lift_ratio = avg_players / baseline_avg
#     ELSE:
#       lift_ratio = NULL
#
#   INSERT IGNORE into EVENT_WINDOW_MONTH
#     (event_id, month_offset, avg_players, lift_ratio)
#
# Log count of rows inserted per event
```

### gen_synthetic_sales.py

```python
# Scope: FY23Q1 through FY26Q4 only (covers 2022-07-01 to 2026-06-30)
# Grain: Daily_Aggregate — one row per (calendar_date × product × device × region × channel)
# is_synthetic = TRUE (column DEFAULT; no need to set explicitly)
# RNG seeded from SYNTHETIC_SEED env var (default 42)
#
# Reconciliation — three independent groups (PBP, IC, MPC):
#   Productivity and Business Processes (PBP): SUM(gross_amount) vs QUARTERLY_FINANCIALS
#   Intelligent Cloud (IC): reconcile independently
#   More Personal Computing (MPC): reconcile independently (Gaming is inside MPC;
#     DO NOT create a separate Gaming reconciliation group — that would double-count)
#   tolerance: 0.1% per (fiscal_year, fiscal_quarter, segment_id) combination
#   ABORT if any combination exceeds 0.1%
#   Print reconciliation report to stdout
#
# No Gaming cost, no Gaming operating income, no Gaming margin computation.
# unit_cost and cost_amount apply only to PBP/IC/MPC products.
#
# Device mix: Xbox 40%, Windows PC 35%, Mobile 15%, Cloud 5%, Mac 5%
# oem non-null only for Windows PC rows
# Seasonality: Black Friday rows × 3, December rows × 1.5
# Event windows: apply lift from EVENT_WINDOW_MONTH during event periods
#
# FK values (product_id, device_id, region_id, channel_id) pre-loaded from DB
# CSV chunks written to data/clean/sales_chunk_NNN.csv
# 100k-row ORDERS/ORDER_ITEM subset also written
#
# After LOAD DATA LOCAL INFILE completes:
#   slope = 0.01 × current_mb per day  (labelled simulation / 1% daily growth)
#   computed_value = ROUND((current_mb + 25 × slope) / 0.9, 2)
#                  = ROUND(current_mb × 1.25 / 0.9, 2)
#   where current_mb = total data+index MB from information_schema.TABLES at this moment
#   INSERT INTO CONFIG (config_key, config_value)
#   VALUES ('DB_ALLOCATED_MB', computed_value)
#   ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)
#   This represents a simulated budget such that 90% fills in ~25 days at 1%/day growth.
```

### 90-Day Backfill (backfill_snapshots.py)

```python
# Read current total (data + index) MB from information_schema as real_size_mb (newest anchor)
# Assume a small reasonable growth slope (e.g., 0.5 MB/day) for backward extrapolation
# Insert 90 rows with snapped_at from (today - 90) to (today - 1)
# Newest row (today - 1): total_mb = real_size_mb (actual current value)
# Earlier rows: total_mb = real_size_mb - slope × days_ago  (floor 1 MB)
# All rows: is_synthetic = TRUE; label as simulated in documentation
# Uses INSERT IGNORE for idempotency (UNIQUE KEY on snapped_at prevents duplicates)
```

---

## Backend Design

### FastAPI App Structure

```
backend/
  app/
    main.py           # app factory, startup, CORS, router registration
    auth.py           # POST /login, JWT, credential map
    db.py             # pyodbc helpers
    telemetry.py      # EXPLAIN ANALYZE, plan parser, background index task
    routers/
      analyst.py      # all /analyst/* and shared analyst endpoints
      engineer.py     # all /engineer/* endpoints
    models/
      requests.py     # Pydantic request models
      responses.py    # Pydantic response models
```

### Auth Flow

```python
# auth.py (using pyjwt)
import jwt  # pyjwt

def create_token(username: str, role: str) -> str:
    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.utcnow() + timedelta(hours=1)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    # raises jwt.ExpiredSignatureError or jwt.InvalidTokenError on failure

# POST /login handler:
# 1. pyodbc.connect as submitted user (ODBC connect-as-user)
# 2. cursor.execute("SELECT CURRENT_ROLE()")
# 3. Parse result:
#    - If result is None, empty string, or the literal 'NONE': fall back to SHOW GRANTS
#    - Strip backticks from role name before comparison
#      e.g. `business_analyst` → business_analyst
# 4. create_token(username, role)
# 5. store (username, password) in credential_map[token]
# 6. return {"access_token": token}
```

**CURRENT_ROLE() edge cases:**
- MySQL may return `'NONE'` (literal string) when no role is active — treat as empty, fall back to SHOW GRANTS.
- Role names may be wrapped in backticks (e.g., `` `business_analyst` ``) — strip backticks before comparing to `'business_analyst'`, `'db_engineer'`, etc.

### Telemetry Flow

```
POST /sql received with sql_text
  │
  ├─ Validate: single SELECT or WITH...SELECT
  │   reject if re.search(r';\s*\S', sql_text)
  │   reject DML at word boundary
  │
  ├─ conn_analyst = get_connection(username, password, role='business_analyst')
  │   SET MAX_EXECUTION_TIME = 30000
  │
  ├─ EXPLAIN ANALYZE execution (conn_analyst):
  │   cursor.execute(f"EXPLAIN ANALYZE {sql_text}")
  │   explain_text = cursor.fetchall()
  │   parse_explain_tree(explain_text) →
  │     For each line:
  │       depth = leading_spaces / 2
  │       pop indent stack until top depth < current depth
  │       parent_node_id = current stack top
  │       push current node
  │       Parse "actual time=N..M loops=L":
  │         actual_ms = M  (per-loop end time; M is the elapsed time for one iteration)
  │         total_ms  = M × L  (total time across all loops)
  │       pct_time  = node.total_ms / root.total_ms × 100
  │       duration_ms (for QUERY_EXECUTION) = root.actual_ms  (M from root, not total_ms)
  │       operation = text before first "  ("
  │       table_name = extracted from "Table scan on <tbl>" (FACT_SALES has no alias)
  │     rows_examined = SUM(actual_rows × loops) for all "Table scan on" nodes
  │
  ├─ Plain execution (conn_analyst):
  │   cursor.execute(sql_text)
  │   result_rows = cursor.fetchall()
  │   rows_sent = len(result_rows)
  │
  ├─ Write QUERY_EXECUTION via conn_telemetry:
  │   call_sp_out(conn_telemetry, "sp_insert_qe",
  │     [db_user, digest_text, started_at, duration_ms, rows_examined, rows_sent], 1)
  │   → qe_id
  │
  ├─ Write QUERY_PLAN_NODE rows via conn_telemetry (one call per node)
  │
  ├─ Flag check: any node.operation.startswith("Table scan on") AND node.pct_time > 50?
  │   YES → call sp_flag_query(qe_id) via conn_telemetry
  │         enqueue BackgroundTask: run_index_recommendation(...)
  │   NO  → flagged = False
  │
  └─ RETURN immediately: {qe_id, flagged, rows: result_rows, duration_ms: root.actual_ms}

Background task: run_index_recommendation(qe_id, table, columns_csv)
  ├─ open fresh analyst connection (for before/after timing)
  ├─ measure before_ms: median of 3 runs of sql_text WITHOUT use_invisible_indexes
  │   (baseline measurement; invisible index not considered by optimizer)
  ├─ call sp_recommend_index(qe_id, table, columns_csv, @out0) via conn_telemetry
  │   (this creates the index as INVISIBLE — not used by optimizer by default)
  │   p_rec_id = returned OUT parameter
  ├─ measure after_ms: median of 3 runs WITH
  │   SET SESSION optimizer_switch='use_invisible_indexes=on'
  │   (simulates index effect before engineer approval)
  └─ call sp_record_index_test(rec_id, before_ms, after_ms) via conn_telemetry
```

**Plan parser notes:**
- `actual_ms` = M, the per-loop end time from `actual time=N..M loops=L`
- `total_ms` = M × L (total time across all loop iterations)
- `pct_time` = `node.total_ms / root.total_ms × 100`
- `duration_ms` stored in QUERY_EXECUTION = root node's `actual_ms` (M, not total_ms)
- The root node's `actual_ms` represents the wall-clock elapsed time for one full execution of the query plan.
- FACT_SALES has no alias in v_sales, so the plan parser resolves the table name from the literal 'Table scan on FACT_SALES' pattern without alias resolution.

**rows_examined note**: `rows_examined` is derived from the sum of `actual_rows × loops` across all 'Table scan on' plan nodes, not from performance_schema.

### API client.js

```javascript
// src/api/client.js
import axios from 'axios';

const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL,
  timeout: 10000
});

let _token = null;

export function setToken(token) { _token = token; }
export function clearToken() { _token = null; }

client.interceptors.request.use(config => {
  if (_token) config.headers.Authorization = `Bearer ${_token}`;
  return config;
});

client.interceptors.response.use(
  response => response,
  error => {
    // Do NOT redirect on 401 for the /login endpoint itself;
    // the login page handles its own 401 display inline.
    const isLoginEndpoint = error.config?.url?.includes('/login');
    if (error.response?.status === 401 && !isLoginEndpoint) {
      clearToken();
      window.location.replace('/login');
    }
    return Promise.reject(error);
  }
);

export default client;
```

**POST /sql timeout**: The SQL console makes its POST /sql call with a 60-second timeout:
```javascript
client.post('/sql', { sql }, { timeout: 60000 })
```

### All API Endpoints

#### Analyst Router

| Method | Path | Action |
|--------|------|--------|
| POST | /login | Authenticate; return JWT |
| GET | /events | SELECT * FROM v_events |
| GET | /margin | SET @group_by = ?; CALL sp_margin_decomposition |
| GET | /forecast/business/{event_id} | SET @as_of_date; CALL sp_forecast_business |
| POST | /sql | Validate; telemetry; background task; return {qe_id, flagged, rows, duration_ms} |
| PUT | /pricing/{rule_id} | CALL sp_update_pricing_rule; 200/409 |
| GET | /my-queries | SELECT * FROM v_my_queries |
| POST | /analyst/orders | CALL sp_place_order |

**GET /margin note**: The `group_by` query parameter is passed as a session variable `SET @group_by = ?` before calling `sp_margin_decomposition`. It is NOT a stored procedure parameter.

#### Engineer Router

| Method | Path | Action |
|--------|------|--------|
| GET | /workload | performance_schema digest stats top 20 |
| GET | /slow-queries | QUERY_EXECUTION WHERE flagged=1 ORDER BY started_at DESC |
| GET | /slow-queries/{qe_id}/plan | QUERY_PLAN_NODE WHERE qe_id=? |
| GET | /index-recs | INDEX_RECOMMENDATION JOIN QUERY_EXECUTION WHERE status IN ('proposed','testing') |
| POST | /index-recs/{id}/approve | CALL sp_apply_index; if after_ms IS NULL return HTTP 409 |
| POST | /index-recs/{id}/reject | CALL sp_reject_index |
| GET | /partitions | SELECT * FROM v_partitions |
| POST | /partitions | CALL sp_add_partition |
| GET | /capacity/forecast | FORECAST (kind='database') + FORECAST_LINE |
| POST | /forecast/database/{event_id} | SET @as_of_date; CALL sp_forecast_database |
| GET | /conflicts | SELECT * FROM CONFLICT_LOG ORDER BY at DESC LIMIT 100 |
| GET | /engineer/events | SELECT * FROM v_events_min |

### as_of_date Pattern

```python
# In both analyst and engineer routers:
if as_of_date:
    cursor.execute("SET @as_of_date = ?", [as_of_date])
else:
    cursor.execute("SET @as_of_date = NULL")
rows = call_sp(conn, "sp_forecast_business", [event_id])
```

### Error Response Format

```json
{
  "detail": "human-readable message",
  "error_code": "DB_UNAVAILABLE | VALIDATION_ERROR | FORBIDDEN | UNAUTHORIZED"
}
```

HTTP 503 when pyodbc raises a connection error. HTTP 422 on Pydantic validation failure. HTTP 401/403 on auth failure. HTTP 409 when index test not yet complete.

---

## Frontend Design

### Page Structure

```
/login                          — public, Login form
/analyst/
  events                        — EventCalendar (fetches GET /events)
  margin                        — MarginDecomposition (fetches GET /margin)
  forecast/:eventId             — BusinessForecast (fetches GET /forecast/business/:id)
  sql                           — SqlConsole (POST /sql with 60s timeout; GET /my-queries)
/engineer/
  workload                      — WorkloadMonitor (GET /workload)
  slow-queries                  — SlowQueryQueue (GET /slow-queries)
  index-recs                    — IndexApproval (GET /index-recs; approve/reject)
  partitions                    — PartitionManager (GET /partitions; POST /partitions)
  forecast                      — CapacityForecast (GET /capacity/forecast)
  events                        — EngineerEventCalendar (GET /engineer/events)
  conflicts                     — ConflictLog (GET /conflicts)
```

### Stitch Integration

- Stitch design exports placed in `frontend/src/stitch_export/`
- Components imported by page files; layout and styles not modified
- Mock data arrays replaced with `useApi` hook calls
- Recharts in MarginDecomposition wired to `/margin` response

---

## Testing Design

### test_roles.sh

```bash
#!/usr/bin/env bash
source .env

assert_denied() {
  local user="$1" pass="$2" query="$3" label="$4"
  result=$(mysql -h "$DB_HOST" -P "$DB_PORT" -u "$user" -p"$pass" \
    "$DB_NAME" -e "$query" 2>&1)
  if echo "$result" | grep -q "ERROR 1142"; then
    echo "PASS: $label denied with ERROR 1142"
  else
    echo "FAIL: $label — expected ERROR 1142, got: $result"
    exit 1
  fi
}

assert_row_count() {
  local user="$1" pass="$2" query="$3" expected="$4" label="$5"
  actual=$(mysql -h "$DB_HOST" -P "$DB_PORT" -u "$user" -p"$pass" \
    "$DB_NAME" --skip-column-names -e "$query" 2>&1)
  if [ "$actual" = "$expected" ]; then
    echo "PASS: $label row count = $expected"
  else
    echo "FAIL: $label — expected $expected rows, got $actual"
    exit 1
  fi
}

# analyst_demo denied queries
assert_denied  "$ANALYST_DEMO_USERNAME"  "$DEMO_ANALYST_PASSWORD"  \
  "SELECT 1 FROM QUERY_PLAN_NODE LIMIT 1"  "analyst_demo→QUERY_PLAN_NODE"
assert_denied  "$ANALYST_DEMO_USERNAME"  "$DEMO_ANALYST_PASSWORD"  \
  "SELECT 1 FROM INDEX_RECOMMENDATION LIMIT 1"  "analyst_demo→INDEX_RECOMMENDATION"
assert_denied  "$ANALYST_DEMO_USERNAME"  "$DEMO_ANALYST_PASSWORD"  \
  "SELECT 1 FROM FACT_SALES LIMIT 1"  "analyst_demo→FACT_SALES"

# engineer_demo denied queries
assert_denied  "$ENGINEER_DEMO_USERNAME"  "$DEMO_ENGINEER_PASSWORD" \
  "SELECT 1 FROM FACT_SALES LIMIT 1"  "engineer_demo→FACT_SALES"
assert_denied  "$ENGINEER_DEMO_USERNAME"  "$DEMO_ENGINEER_PASSWORD" \
  "SELECT 1 FROM ORDERS LIMIT 1"  "engineer_demo→ORDERS"
assert_denied  "$ENGINEER_DEMO_USERNAME"  "$DEMO_ENGINEER_PASSWORD" \
  "SELECT 1 FROM QUARTERLY_FINANCIALS LIMIT 1"  "engineer_demo→QUARTERLY_FINANCIALS"

# analyst_demo segment filter (Gaming only)
EXPECTED_COUNT=$(mysql -h "$DB_HOST" -P "$DB_PORT" -u "$ADMIN_USER" \
  "$DB_NAME" --skip-column-names -e \
  "SELECT COUNT(*) FROM FACT_SALES fs JOIN PRODUCT p ON fs.product_id=p.product_id
   JOIN COMPANY_SEGMENT cs ON p.segment_id=cs.segment_id
   WHERE cs.name='Gaming'")
assert_row_count "$ANALYST_DEMO_USERNAME" "$DEMO_ANALYST_PASSWORD" \
  "SELECT COUNT(*) FROM v_sales" "$EXPECTED_COUNT" "analyst_demo v_sales Gaming filter"

echo "All role tests passed."
```

### test_concurrency.py

```python
import threading, requests, pytest

def test_optimistic_lock_conflict():
    base = "http://localhost:8000"
    # Read current version from PRICING_RULE before spawning threads
    current_version = admin_query(
        "SELECT version FROM PRICING_RULE WHERE rule_id = 1"
    )
    token_a = login(base, "analyst_demo", DEMO_ANALYST_PASSWORD)
    token_b = login(base, "analyst_all",  DEMO_ANALYST_ALL_PASSWORD)

    results = []
    def update(token, val):
        r = requests.put(f"{base}/pricing/1",
            headers={"Authorization": f"Bearer {token}"},
            json={"new_value": val, "expected_version": current_version})
        results.append(r.status_code)

    t1 = threading.Thread(target=update, args=(token_a, 9.99))
    t2 = threading.Thread(target=update, args=(token_b, 8.99))
    t1.start(); t2.start()
    t1.join();  t2.join()

    assert sorted(results) == [200, 409]
    # Verify exactly one CONFLICT_LOG row (via admin)
    conflict_count = admin_query(
        "SELECT COUNT(*) FROM CONFLICT_LOG WHERE rule_id = 1 AND stale_version = ?",
        [current_version]
    )
    assert conflict_count == 1
    # Cleanup at teardown
    admin_exec("DELETE FROM CONFLICT_LOG WHERE rule_id = 1 AND stale_version = ?",
               [current_version])
```

### test_demo_loop.py

```python
import time, requests, pytest

MAX_TOTAL_S = 120
POLL_INDEX_REC_S = 60
POLL_INDEX_APPLIED_S = 30

def test_demo_loop():
    start = time.time()

    # 0. Drop prior test index via admin connection
    admin_exec("ALTER TABLE FACT_SALES DROP INDEX IF EXISTS idx_rec_...")

    # 1. Submit Heavy_Demo_Query; record pre-index duration_ms from API response
    sql = open("tests/heavy_demo_query.sql").read()
    r = analyst_post("/sql", {"sql": sql})
    qe_id = r["qe_id"]
    pre_ms = r["duration_ms"]  # from QUERY_EXECUTION via API response

    # 2. Poll for index recommendation
    rec_id = None
    deadline = time.time() + POLL_INDEX_REC_S
    while time.time() < deadline:
        recs = engineer_get("/index-recs")
        for rec in recs:
            if rec["qe_id"] == qe_id:
                rec_id = rec["rec_id"]
                break
        if rec_id: break
        time.sleep(2)
    assert rec_id is not None, "No recommendation appeared within 60s"

    # 3. Approve
    r = engineer_post(f"/index-recs/{rec_id}/approve")
    assert r.status_code == 200

    # 4. Poll for index in information_schema.STATISTICS (admin connection)
    deadline2 = time.time() + POLL_INDEX_APPLIED_S
    index_visible = False
    while time.time() < deadline2:
        count = admin_query(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA=? AND TABLE_NAME=? AND INDEX_NAME=?",
            [DB_NAME, "FACT_SALES", f"idx_rec_{rec_id}"]
        )
        if count > 0:
            index_visible = True
            break
        time.sleep(1)
    assert index_visible, "Index not visible in information_schema within 30s"

    # 5. Rerun 2× via POST /sql; compare duration_ms from API responses (not wall-clock)
    post_durations = []
    for _ in range(2):
        r = analyst_post("/sql", {"sql": sql})
        post_durations.append(r["duration_ms"])

    for post_ms in post_durations:
        assert post_ms <= pre_ms * 0.5, \
            f"Speedup insufficient: pre={pre_ms}ms, post={post_ms}ms"

    # 6. Generate DB forecast (must call POST before GET)
    future_event_id = get_future_game_launch_event_id()
    engineer_post(f"/forecast/database/{future_event_id}")

    # 7. Capacity forecast sanity
    fc = engineer_get("/capacity/forecast")
    assert fc["days_to_90pct"] is not None
    at_risk = [q for q in fc.get("queries", []) if q["projected_ms"] >= q["baseline_ms"] * AT_RISK_FACTOR]
    assert len(at_risk) >= 1

    total_s = time.time() - start
    assert total_s <= MAX_TOTAL_S, f"Demo loop took {total_s:.1f}s > {MAX_TOTAL_S}s"
    # Note: AT_RISK_FACTOR is read from CONFIG table (default 1.25)
```

---

## MySQL Behaviour Verification

Each behaviour must be verified on the target MySQL 8.0 instance before implementation begins; results recorded in the relevant stage report.

| Behaviour | Verification Method | Expected Result |
|-----------|--------------------|--------------| 
| EXPLAIN ANALYZE output format | Run `EXPLAIN ANALYZE SELECT 1` | Lines prefixed with `->` at varying indentation; each node shows `actual time=N..M rows=R loops=L` |
| Invisible indexes | Create test table, add INVISIBLE index, run query without optimizer hint — index not used; set `optimizer_switch='use_invisible_indexes=on'` — index used; verify VISIBLE/INVISIBLE status in information_schema.STATISTICS | Confirmed via EXPLAIN output and STATISTICS table |
| REORGANIZE PARTITION | Create table with MAXVALUE partition; REORGANIZE INTO two partitions; verify in information_schema.PARTITIONS | New partitions appear; old p_catch replaced |
| information_schema stats refresh | SET SESSION information_schema_stats_expiry=0; run INSERT; re-query information_schema.TABLES | data_length updates immediately within same session |
| SHOW VIEW for EXPLAIN on views | GRANT SHOW VIEW to test role; run EXPLAIN on a view as that role | No ERROR 1142; EXPLAIN returns plan |
| CREATE TABLE IF NOT EXISTS idempotency | Run 01_ddl.sql twice on a populated schema | No errors on second run; existing data preserved |
| CURRENT_ROLE() output format | Run `SELECT CURRENT_ROLE()` as analyst_demo | Record whether result is backtick-wrapped, plain, or 'NONE' |

---

## .gitignore

```
.env
__pycache__/
*.pyc
node_modules/
data/clean/
data/raw/msft_facts.json
.venv/
dist/
!data/raw/events.csv
!data/raw/segments.csv
```

Note: The former `*.sql` blanket rule has been removed — it incorrectly ignored all migration and DDL files. mysqldump backup files are written outside the repository to `~/margin_db_backups/` to avoid large files in git history. If dump files need to be excluded, add a targeted rule such as `backups/*.sql` or `data/raw/*.sql`.

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Optimistic Lock Exclusivity

*For any* pricing rule and any set of concurrent updates with the same expected_version, exactly one update SHALL succeed (HTTP 200, version incremented) and all others SHALL fail (HTTP 409, CONFLICT_LOG row inserted). After N concurrent attempts with the same expected_version, the rule version SHALL equal initial_version + 1, and CONFLICT_LOG SHALL contain exactly N−1 rows for that rule.

**Validates: Requirements 12.1, 12.2, 12.3**

---

### Property 2: pct_time Sum Invariant

*For any* EXPLAIN ANALYZE output parsed by `parse_explain_tree`, the root node SHALL have `pct_time = 100.0`; every non-root node's `pct_time` SHALL equal `(actual_ms × loops) / root_total_ms × 100` (where `root_total_ms = root_actual_ms × root_loops`) within a tolerance of 0.01; `duration_ms` stored in QUERY_EXECUTION SHALL equal the root node's `actual_ms` (M, the per-loop end time of the root node), not `root_total_ms`.

**Validates: Requirements 7.1**

---

### Property 3: Full-Scan Flag Rule Completeness

*For any* set of QUERY_PLAN_NODE rows, a QUERY_EXECUTION SHALL be flagged (flagged=1) if and only if at least one node has an operation beginning with "Table scan on" AND `pct_time > 50`. No false positives (flagging when rule does not apply) and no false negatives (missing a flag when rule does apply) are permitted.

**Validates: Requirements 7.4, 11.2**

---

### Property 4: Transactional Order Atomicity

*For any* valid JSON items array passed to `sp_place_order`, either all three inserts (ORDERS, ORDER_ITEM, FACT_SALES) SHALL succeed atomically, or none of them SHALL produce persistent rows. After a rollback triggered by the EXIT HANDLER, the COUNT(*) of ORDERS, ORDER_ITEM, and FACT_SALES SHALL be identical to the pre-call counts.

**Validates: Requirements 13.1**

---

### Property 5: Projected Duration Formula Correctness

*For any* positive values of `baseline_ms`, `current_rows`, and `projected_rows`, the computed `projected_duration_ms` SHALL equal `baseline_ms × (projected_rows / current_rows)` within floating-point tolerance; a query SHALL be marked at-risk if and only if `projected_duration_ms >= AT_RISK_FACTOR × baseline_ms` where AT_RISK_FACTOR is read from CONFIG (default 1.25).

**Validates: Requirements 9.3, 9.4**

---

### Property 6: SQL Console Multi-Statement Rejection

*For any* SQL string submitted to `POST /sql`, the validation function SHALL return True (accept) if and only if the string matches the grammar: `(WITH [name] AS (...))? SELECT ...` with no trailing semicolon followed by non-whitespace. The function SHALL return False (reject) for: multi-statement strings (semicolon followed by non-whitespace), DML keywords at word boundaries, and non-SELECT single statements. A grammar-based property-testing strategy generates valid and invalid inputs from these production rules.

**Validates: Requirements 16.1**

---

### Property 7: Segment Row Filter Invariant

*For all* queries through segment-filtered views (`v_sales`, `v_financials`) executed by any demo user, the result set SHALL contain only rows where `segment_id` is in the set of segments mapped to that user in `USER_SEGMENT_MAP`. No rows from other segments SHALL appear. `analyst_demo`'s segment mapping is pending owner decision (see MARGIN_DB.md §12.4 Q3). Note: `v_my_queries` is filtered by `db_user` (per-user filter) not by segment.

**Validates: Requirements 2.1, 2.7**

---

## ETL Validation Summary

| Script | Validates | On Failure |
|--------|-----------|-----------|
| fetch_edgar.py | HTTP 200 from SEC EDGAR companyfacts endpoint; Q4 derivable from FY annual | Log UTC+URL; exit 1 |
| load_financials.py | All three metrics present; fiscal quarter frame only | Skip period; log |
| load_steam.py | obs_date ISO 8601 AND must be 1st of month; avg_players non-negative **decimal** (DECIMAL(12,2)); peak_players non-negative integer | Skip row; log identifier/field/expected/actual |
| load_trends.py | pytrends available or manual CSV present | Log rate-limit; fall back; exit 1 if neither |
| load_events.py | source_url non-null; end_date >= start_date; event_type in ENUM; future game_launch | Skip row; log; warn if no future event |
| compute_event_windows.py | game_launch events with non-NULL steam_app_id only; reads EVENT_PRE_MONTHS, EVENT_POST_MONTHS, BASELINE_MONTHS, MIN_BASELINE_MONTHS from CONFIG; lift_ratio NULL when fewer than MIN_BASELINE_MONTHS baseline months exist; missing months logged to stdout and data/clean/lift_warnings_<event_id>.log | Set lift_ratio = NULL for insufficient baseline; never zero or interpolated; log per event |
| gen_synthetic_sales.py | Reconciliation within 0.1% per (fy, fq, segment_id) for three independent groups | Print report; ABORT |
