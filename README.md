# Margin-DB

A full-stack database-intelligence demonstration system built on MySQL 8.0, modelling Microsoft's Xbox/Gaming business unit using real SEC EDGAR financial data, synthetic sales transactions, and real-world game-launch events.

---

## Prerequisites

| Tool | Minimum version |
|------|----------------|
| Python | 3.12 |
| Node | 18 |
| MySQL Server | 8.0.18 |
| MySQL ODBC 8.0 Unicode Driver | 8.0 |
| unixODBC (macOS/Linux) | 2.3.9 |

---

## MySQL Stored Procedure Syntax Note

MySQL uses `DELIMITER` to define multi-statement procedures; there is no `DECLARE...EXCEPTION` block, no `DBMS_*` packages, no `:=` operator, and no `RAISE_APPLICATION_ERROR`. Error handling uses `DECLARE ... HANDLER FOR SQLEXCEPTION BEGIN ... END`.

---

## Weak Entities

This schema has four weak entities: ORDER_ITEM (owner ORDERS), EVENT_WINDOW_DAY (owner BUSINESS_EVENT), QUERY_PLAN_NODE (owner QUERY_EXECUTION), FORECAST_LINE (owner FORECAST)

---

## Platform Notes

Run `uname -m` to identify your host architecture. Apple Silicon (arm64) and x86_64 Intel are both supported, but the MySQL ODBC driver must match your architecture. On macOS arm64, install the arm64 variant of the MySQL ODBC 8.0 Unicode Driver.

```bash
uname -m   # arm64 (Apple Silicon) or x86_64 (Intel)
```

---

## Setup

### 1. Copy and populate `.env`

```bash
cp .env.example .env
# Edit .env and fill in every placeholder value:
#   DB_HOST, DB_PORT, DB_NAME, ADMIN_USER, ADMIN_PASSWORD,
#   SVC_TELEMETRY_USER, SVC_TELEMETRY_PASSWORD, SEC_USER_AGENT,
#   JWT_SECRET, DEMO_ANALYST_PASSWORD, DEMO_ANALYST_ALL_PASSWORD,
#   DEMO_ENGINEER_PASSWORD, SYNTHETIC_SEED
```

### 2. Create and activate the Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate   # macOS / Linux
# .venv\Scripts\activate    # Windows
```

### 3. Install backend dependencies

```bash
pip install -r backend/requirements.txt
```

### 4. Install ETL dependencies

```bash
pip install -r etl/requirements.txt
```

### 5. Install frontend dependencies

```bash
cd frontend
npm install
cd ..
```

---

## Stage-by-Stage Run Instructions

### Stage 0 — Environment

**HUMAN STEP**: Log in to MySQL as root and execute:

```sql
CREATE DATABASE IF NOT EXISTS margin_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'admin_user'@'localhost' IDENTIFIED BY '<password>';
GRANT ALL ON margin_db.* TO 'admin_user'@'localhost' WITH GRANT OPTION;
GRANT CREATE USER, CREATE ROLE, ROLE_ADMIN ON *.* TO 'admin_user'@'localhost';
GRANT SELECT ON performance_schema.* TO 'admin_user'@'localhost' WITH GRANT OPTION;
SET GLOBAL event_scheduler = ON;
SET GLOBAL local_infile = 1;
```

Update `.env` with `ADMIN_USER`, `ADMIN_PASSWORD`, `DB_NAME=margin_db`, then run the ODBC connection test:

```bash
source .venv/bin/activate
pytest tests/test_odbc.py::test_odbc_connection tests/test_odbc.py::test_parameterized_query
```

Record MySQL version, `performance_schema` ON/OFF, and host architecture in `docs/stage_0_report.md`:

```bash
uname -m
mysql -u admin_user -p -e "SELECT VERSION(); SHOW VARIABLES LIKE 'performance_schema';"
```

Commit:

```bash
git add -A && git commit -m "Stage 0: environment"
```

---

### Stage 1 — Schema

Apply all seven migration files in order (halts on any error):

```bash
bash sql/run_migrations.sh
```

Run role permission tests and ODBC tests:

```bash
bash tests/run_role_tests.sh
pytest tests/test_odbc.py
```

Verify `FACT_SALES` has 21 partitions (20 named + `p_catch`):

```bash
mysql -u admin_user -p margin_db \
  -e "SELECT PARTITION_NAME FROM information_schema.PARTITIONS
      WHERE TABLE_SCHEMA='margin_db' AND TABLE_NAME='FACT_SALES';"
```

Write `docs/stage_1_report.md` and commit:

```bash
git add -A && git commit -m "Stage 1: schema, roles, views, routines stubs, grants"
```

---

### Stage 2 — ETL Pipeline

**HUMAN STEP**: Provide `data/raw/segments.csv`, `data/raw/steam_players.csv`, and `data/raw/events.csv` (events.csv must include at least one `game_launch` with a future `start_date`).

Activate venv and run the full ETL orchestrator (30-minute timeout):

```bash
source .venv/bin/activate
bash etl/load_sales.sh
```

Verify row counts and reconciliation output, then run the Stage 2 gate (sequential-scan verification):

```bash
# Run the Heavy_Demo_Query via MySQL CLI as analyst_all (> 3 s expected)
mysql -u analyst_all -p$DEMO_ANALYST_ALL_PASSWORD margin_db < tests/heavy_demo_query.sql

# Manually create candidate index, verify >= 50% speedup, then drop it
mysql -u admin_user -p margin_db \
  -e "CREATE INDEX idx_stage2_test ON FACT_SALES (product_id, sale_date) ALGORITHM=INPLACE;"
mysql -u analyst_all -p$DEMO_ANALYST_ALL_PASSWORD margin_db < tests/heavy_demo_query.sql
mysql -u admin_user -p margin_db \
  -e "DROP INDEX idx_stage2_test ON FACT_SALES;"
```

Write `docs/stage_2_report.md`, back up the database, and commit:

```bash
mkdir -p ~/margin_db_backups
mysqldump -u $ADMIN_USER $DB_NAME > ~/margin_db_backups/margin_db_stage2.sql
git add -A && git commit -m "Stage 2: ETL complete"
```

---

### Stage 3 — Stored Routines and DB Intelligence

Apply full stored procedure implementations:

```bash
bash sql/run_migrations.sh
```

Run the 90-day snapshot backfill:

```bash
source .venv/bin/activate
python etl/backfill_snapshots.py
```

Verify the `ev_snapshot_sizes` event is enabled:

```bash
mysql -u admin_user -p margin_db \
  -e "SELECT EVENT_NAME, STATUS FROM information_schema.EVENTS WHERE EVENT_SCHEMA='margin_db';"
```

Write `docs/stage_3_report.md` and commit:

```bash
git add -A && git commit -m "Stage 3: stored routines and DB intelligence"
```

---

### Stage 4 — Backend API

Start the backend:

```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Visit `http://localhost:8000/docs` for the interactive OpenAPI UI.

Confirm the login endpoint works:

```bash
# Obtain a JWT for analyst_demo
curl -s -X POST http://localhost:8000/login \
  -H "Content-Type: application/json" \
  -d '{"username": "analyst_demo", "password": "<DEMO_ANALYST_PASSWORD>"}' | python3 -m json.tool

# Obtain a JWT for engineer_demo
curl -s -X POST http://localhost:8000/login \
  -H "Content-Type: application/json" \
  -d '{"username": "engineer_demo", "password": "<DEMO_ENGINEER_PASSWORD>"}' | python3 -m json.tool
```

Write `docs/stage_4_report.md` and commit:

```bash
git add -A && git commit -m "Stage 4: backend API"
```

---

### Stage 5 — Frontend

Start the Vite dev server (requires the backend running on port 8000):

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173` in a browser. Build for production to verify no compile errors:

```bash
cd frontend
npm run build
```

Write `docs/stage_5_report.md` and commit:

```bash
git add -A && git commit -m "Stage 5: frontend"
```

---

## Demo Loop

The demo illustrates the full query-intelligence cycle: a slow analyst query triggers automatic index recommendation, which the engineer approves, producing a measurable speedup.

### Step 1 — Obtain tokens

```bash
ANALYST_TOKEN=$(curl -s -X POST http://localhost:8000/login \
  -H "Content-Type: application/json" \
  -d '{"username":"analyst_demo","password":"<DEMO_ANALYST_PASSWORD>"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

ENGINEER_TOKEN=$(curl -s -X POST http://localhost:8000/login \
  -H "Content-Type: application/json" \
  -d '{"username":"engineer_demo","password":"<DEMO_ENGINEER_PASSWORD>"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

### Step 2 — Submit the Heavy_Demo_Query (analyst)

```bash
curl -s -X POST http://localhost:8000/sql \
  -H "Authorization: Bearer $ANALYST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT d.fiscal_year, d.fiscal_quarter, r.region_name, SUM(fs.gross_amount) AS revenue FROM v_sales fs JOIN DIM_DATE d ON fs.sale_date = d.full_date JOIN DIM_REGION r ON fs.region_id = r.region_id WHERE d.fiscal_year IN (2023,2024,2025,2026) GROUP BY d.fiscal_year, d.fiscal_quarter, r.region_name ORDER BY d.fiscal_year, d.fiscal_quarter"}' \
  | python3 -m json.tool
# Returns: { "qe_id": <N>, "flagged": true, "rows": [...], "duration_ms": <>}
```

### Step 3 — View slow-query queue (engineer)

```bash
curl -s http://localhost:8000/slow-queries \
  -H "Authorization: Bearer $ENGINEER_TOKEN" \
  | python3 -m json.tool
```

### Step 4 — List and approve index recommendation (engineer)

```bash
# List pending recommendations
curl -s http://localhost:8000/index-recs \
  -H "Authorization: Bearer $ENGINEER_TOKEN" \
  | python3 -m json.tool

# Approve (replace <REC_ID> with the id from the list above)
curl -s -X POST http://localhost:8000/index-recs/<REC_ID>/approve \
  -H "Authorization: Bearer $ENGINEER_TOKEN" \
  | python3 -m json.tool
```

### Step 5 — Resubmit the query and verify speedup (analyst)

```bash
curl -s -X POST http://localhost:8000/sql \
  -H "Authorization: Bearer $ANALYST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT d.fiscal_year, d.fiscal_quarter, r.region_name, SUM(fs.gross_amount) AS revenue FROM v_sales fs JOIN DIM_DATE d ON fs.sale_date = d.full_date JOIN DIM_REGION r ON fs.region_id = r.region_id WHERE d.fiscal_year IN (2023,2024,2025,2026) GROUP BY d.fiscal_year, d.fiscal_quarter, r.region_name ORDER BY d.fiscal_year, d.fiscal_quarter"}' \
  | python3 -m json.tool
# duration_ms should be >= 50% lower than the first run
```

### Step 6 — Run the automated demo-loop test

```bash
source .venv/bin/activate
pytest tests/test_demo_loop.py -v
# Must complete within 120 seconds
```

---

## Resetting the Database

A destructive reset script is provided at `sql/reset_db.sql`. It must be run manually with explicit confirmation — it is **not** invoked by `run_migrations.sh`:

```bash
mysql -u admin_user -p margin_db < sql/reset_db.sql
# The script prompts: "Type YES to reset margin_db"
```
