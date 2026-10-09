# Stage 1 Report — Schema, Roles, Views, Routine Stubs, Grants

**Date**: 2026-10-09 UTC  
**Platform**: macOS arm64 (Apple Silicon)  
**MySQL version**: 26.7.0 (Homebrew)  
**Host**: Tejaswis-MacBook-Air.local  
**performance_schema**: ON  

---

## 1. Migration Gate — `sql/run_migrations.sh`

All 7 migration files applied without error on **two consecutive runs** (idempotency verified).

### Run 1 (first-time apply)

```
Applying sql/01_ddl.sql ...   OK
Applying sql/02_roles.sql ... OK
Applying sql/03_views.sql ... OK
Applying sql/04_routines.sql ... OK
Applying sql/05_triggers_events.sql ... OK
Applying sql/06_seed_reference.sql ... OK
Applying sql/07_grants.sql ... OK
All migrations applied successfully.
```

### Run 2 (idempotency check — re-applied immediately after Run 1)

```
All migrations applied successfully.
```

`CREATE TABLE IF NOT EXISTS` prevents duplicate-table errors on re-run.  
`DROP VIEW IF EXISTS` + `CREATE VIEW`, `DROP PROCEDURE IF EXISTS` + `CREATE PROCEDURE`,  
`DROP TRIGGER IF EXISTS` + `CREATE TRIGGER`, `INSERT IGNORE` in seed data — all idempotent.

### Issues fixed during this stage

| Issue | Fix |
|-------|-----|
| `CREATE VIEW v_sales SQL SECURITY DEFINER AS` — wrong syntax | Changed to `CREATE SQL SECURITY DEFINER VIEW v_sales AS` (MySQL requires modifier before `VIEW` keyword) |
| `log_bin_trust_function_creators = OFF` blocked trigger/routine creation | Added `/opt/homebrew/etc/my.cnf` with `log_bin_trust_function_creators = 1`; restarted MySQL |
| Users created as `@'%'` in `02_roles.sql` but grants in `07_grants.sql` targeted `@'localhost'` | Changed `02_roles.sql` to create users with `@'localhost'` host |

---

## 2. pytest ODBC Tests

Command:
```bash
source .env && source .venv/bin/activate && pytest tests/test_odbc.py::test_odbc_connection tests/test_odbc.py::test_parameterized_query -v
```

Result:
```
tests/test_odbc.py::test_odbc_connection       PASSED
tests/test_odbc.py::test_parameterized_query   PASSED
2 passed in 0.04s
```

- **test_odbc_connection**: Connected via pyodbc; `SELECT VERSION()` returned `26.7.0`; major version ≥ 8 ✓
- **test_parameterized_query**: `SELECT ?` with string binding returned exact input ✓

---

## 3. Role Tests — `tests/run_role_tests.sh`

Command:
```bash
bash tests/run_role_tests.sh
```

Result:
```
--- Denial checks: analyst_demo ---
PASS  analyst_demo denied SELECT on QUERY_PLAN_NODE
PASS  analyst_demo denied SELECT on INDEX_RECOMMENDATION
PASS  analyst_demo denied SELECT on FACT_SALES

--- Denial checks: engineer_demo ---
PASS  engineer_demo denied SELECT on FACT_SALES
PASS  engineer_demo denied SELECT on ORDERS
PASS  engineer_demo denied SELECT on QUARTERLY_FINANCIALS

--- Row-count check: analyst_demo v_sales matches Gaming-only rows ---
PASS  analyst_demo v_sales row count matches Gaming-only rows (expected=0) (count=0)

Results: 7 passed, 0 failed
```

**Note**: `analyst_demo v_sales` row count = 0 is expected at this stage — no ETL data has been loaded yet. Once `gen_synthetic_sales.py` runs in Stage 2, this assertion will verify the segment filter restricts analyst_demo to Gaming rows only.

---

## 4. FACT_SALES Partition Verification

Command:
```sql
SELECT PARTITION_NAME, PARTITION_ORDINAL_POSITION, PARTITION_DESCRIPTION
FROM information_schema.PARTITIONS
WHERE TABLE_SCHEMA = 'margin_db'
  AND TABLE_NAME = 'FACT_SALES'
ORDER BY PARTITION_ORDINAL_POSITION;
```

Result: **21 partitions** (20 named quarterly + p_catch MAXVALUE) ✓

| Partition | Position | Boundary (TO_DAYS) |
|-----------|----------|--------------------|
| p_FY23Q1  | 1        | 738794             |
| p_FY23Q2  | 2        | 738886             |
| p_FY23Q3  | 3        | 738976             |
| p_FY23Q4  | 4        | 739067             |
| p_FY24Q1  | 5        | 739159             |
| p_FY24Q2  | 6        | 739251             |
| p_FY24Q3  | 7        | 739342             |
| p_FY24Q4  | 8        | 739433             |
| p_FY25Q1  | 9        | 739525             |
| p_FY25Q2  | 10       | 739617             |
| p_FY25Q3  | 11       | 739707             |
| p_FY25Q4  | 12       | 739798             |
| p_FY26Q1  | 13       | 739890             |
| p_FY26Q2  | 14       | 739982             |
| p_FY26Q3  | 15       | 740072             |
| p_FY26Q4  | 16       | 740163             |
| p_FY27Q1  | 17       | 740255             |
| p_FY27Q2  | 18       | 740347             |
| p_FY27Q3  | 19       | 740437             |
| p_FY27Q4  | 20       | 740528             |
| p_catch   | 21       | MAXVALUE           |

---

## 5. MySQL Behaviour Verification

### 5a. EXPLAIN ANALYZE Format

Command:
```sql
EXPLAIN ANALYZE SELECT 1;
```

Output:
```
-> Rows fetched before execution  (cost=0..0 rows=1) (actual time=41e-6..82e-6 rows=1 loops=1)
```

**Observations**:
- Tree-format output; each node indented with `->` prefix
- Timing format: `actual time=N..M` where N = start time, M = end time (elapsed for one loop iteration), both in milliseconds
- Scientific notation used for sub-millisecond values (e.g., `41e-6` = 0.000041 ms)
- `rows=R loops=L` follow the timing values on the same line
- `total_ms` for a node = M × L (end_time × loops)
- `duration_ms` for `QUERY_EXECUTION` row = root node's M value (per-loop end time)

**Implication for Telemetry_Service**: Parse `actual time=N..M rows=R loops=L` from each plan line. Extract M (not N) as `actual_ms` per loop, multiply by L for `total_ms`. Handle scientific notation (e.g., `41e-6` → float conversion).

---

### 5b. Invisible Indexes

**Setup**: Created `_stage1_test_invisible` with `idx_val_invisible (val) INVISIBLE`.

**EXPLAIN without invisible index** (default — optimizer skips it):
```
-> Filter: (_stage1_test_invisible.val = 100)  (cost=0.55 rows=1)
    -> Table scan on _stage1_test_invisible  (cost=0.55 rows=3)
```
→ Full table scan; invisible index ignored. ✓

**EXPLAIN with `SET SESSION optimizer_switch='use_invisible_indexes=on'`**:
```
-> Covering index lookup on _stage1_test_invisible using idx_val_invisible (val = 100)  (cost=0.35 rows=1)
```
→ Optimizer uses the invisible index when explicitly enabled. ✓

**`information_schema.STATISTICS` visibility**:
```sql
SELECT INDEX_NAME, IS_VISIBLE
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA='margin_db' AND TABLE_NAME='_stage1_test_invisible';
```
| INDEX_NAME        | IS_VISIBLE |
|-------------------|------------|
| idx_val_invisible | NO         |
| PRIMARY           | YES        |

`IS_VISIBLE = NO` confirms INVISIBLE status is recorded. ✓  
Test table dropped after verification.

---

### 5c. REORGANIZE PARTITION

**Test table**: `_stage1_test_reorg` with partitions `p_2023` + `p_catch (MAXVALUE)`.

**Before REORGANIZE** (2 partitions):
| PARTITION_NAME | PARTITION_DESCRIPTION |
|----------------|----------------------|
| p_2023         | 739251               |
| p_catch        | MAXVALUE             |

**Command**:
```sql
ALTER TABLE _stage1_test_reorg REORGANIZE PARTITION p_catch INTO (
    PARTITION p_2024 VALUES LESS THAN (TO_DAYS('2025-01-01')),
    PARTITION p_future VALUES LESS THAN MAXVALUE
);
```

**After REORGANIZE** (3 partitions):
| PARTITION_NAME | PARTITION_DESCRIPTION |
|----------------|----------------------|
| p_2023         | 739251               |
| p_2024         | 739617               |
| p_future       | MAXVALUE             |

REORGANIZE PARTITION successfully splits the MAXVALUE catch-all into a named + new catch-all. ✓  
Test table dropped after verification.

---

### 5d. SHOW VIEW — EXPLAIN on a View

**Setup**: Created role `test_showview_role` with `GRANT SHOW VIEW ON margin_db.v_events` + `GRANT SELECT ON margin_db.v_events` + `GRANT SELECT ON margin_db.BUSINESS_EVENT`.  
Created user `test_showview_user@localhost` with that role.

**Command** (as test_showview_user):
```sql
EXPLAIN SELECT * FROM v_events;
```

**Result**:
```
-> Table scan on business_event  (cost=0.35 rows=1)
```
No ERROR 1142. ✓

**Key finding**: `SHOW VIEW` alone on a view is not sufficient for `EXPLAIN` — the user also needs `SELECT` on the underlying base table. Without `SELECT ON BUSINESS_EVENT`, MySQL raises `ERROR 1345 (HY000): EXPLAIN/SHOW can not be issued; lacking privileges for underlying table`. This is distinct from ERROR 1142 (access denied to the view itself).

**Implication for 07_grants.sql**: The `db_engineer` role has `SHOW VIEW` on all `v_*` views; for `EXPLAIN` on views to work at Stage 3, `db_engineer` will also need SELECT on the underlying base tables. This is flagged as an open issue for the backend stage.

Test role and user dropped after verification.

---

### 5e. CREATE TABLE IF NOT EXISTS Idempotency

Second run of `sql/run_migrations.sh` completed without errors. Confirmed:
- `CREATE TABLE IF NOT EXISTS` is truly idempotent ✓
- `DROP VIEW IF EXISTS` + `CREATE VIEW` is idempotent ✓
- `DROP PROCEDURE IF EXISTS` + `CREATE PROCEDURE` is idempotent ✓
- `DROP TRIGGER IF EXISTS` + `CREATE TRIGGER` is idempotent ✓
- `INSERT IGNORE` in seed data is idempotent ✓

---

### 5f. CURRENT_ROLE() Format as analyst_demo

Command:
```sql
SELECT CURRENT_ROLE();
```

Result:
```
+------------------------+
| CURRENT_ROLE()         |
+------------------------+
| `business_analyst`@`%` |
+------------------------+
```

**Format**: Backtick-wrapped role name with `@`%`` suffix — e.g., `` `business_analyst`@`%` ``.

**Implication for backend login flow** (Requirement 1.3): When parsing `SELECT CURRENT_ROLE()` output, the system must strip backticks from the role name and ignore the `@'%'` host suffix before comparing to known role values (`business_analyst`, `db_engineer`, `role_telemetry`). Parsing logic:
```python
raw = "`business_analyst`@`%`"
role = raw.split("@")[0].strip("`")  # → "business_analyst"
```

---

### 5g. sp_insert_qe ODBC Stub Callable via svc_telemetry

**Connection**: pyodbc as `svc_telemetry@localhost` using `MySQL ODBC 8.0 Unicode Driver`

**Call**:
```python
cursor.execute(
    "{CALL sp_insert_qe(?,?,?,?,?,?,@p_qe_id)}",
    ("svc_telemetry", "SELECT 1 /* stage1_test */",
     "2024-01-01 00:00:00.000", 1.234, 1, 1)
)
conn.commit()
while cursor.nextset(): pass
cursor.execute("SELECT @p_qe_id")
row = cursor.fetchone()  # → (None,)
```

**Result**: CALL executed without error; `@p_qe_id` session variable is accessible and returned as `None`.

**Note**: `None` (NULL) is the expected value for the Stage 1 stub (`SET p_qe_id = NULL`). The important verification is that:
1. The ODBC call syntax `{CALL sp_insert_qe(?,?,?,?,?,?,@p_qe_id)}` works ✓
2. `svc_telemetry` has EXECUTE privilege on `sp_insert_qe` ✓
3. `conn.commit()` + `cursor.nextset()` drain + `SELECT @p_qe_id` pattern works ✓

When `sp_insert_qe` is fully implemented in Stage 3, `@p_qe_id` will return the new `QUERY_EXECUTION.qe_id`.

---

## 6. GRANT Statement Placement Verification

Command:
```bash
grep -in "^\s*GRANT " sql/01_ddl.sql sql/02_roles.sql sql/03_views.sql \
     sql/04_routines.sql sql/05_triggers_events.sql sql/06_seed_reference.sql
```

Result: **No output** — zero GRANT statements in files 01–06. ✓

`sql/07_grants.sql` contains **51 GRANT statements** (roles to privileges, roles to users). ✓

---

## 7. Open Issues

| # | Issue | Impact | Mitigation |
|---|-------|--------|------------|
| 1 | EXPLAIN on views requires SELECT on underlying base tables in addition to SHOW VIEW | `db_engineer` cannot EXPLAIN views via backend without additional table grants | Add SELECT grants to base tables for `db_engineer` when implementing the engineer router in Stage 4 |
| 2 | `analyst_demo v_sales` row count = 0 (no ETL data) | Role test passes trivially | Will be a meaningful assertion after Stage 2 ETL loads synthetic sales |
| 3 | `sp_insert_qe` returns NULL (stub) | Telemetry ODBC layer test is partial | Fully implemented in Stage 3 task 3.x |
| 4 | `information_schema_stats_expiry` behaviour deferred | Not tested at Stage 1 | Deferred to Stage 3 gate per spec (when `sp_snapshot_sizes` is implemented) |
| 5 | `CURRENT_ROLE()` returns `` `business_analyst`@`%` `` (backtick-wrapped with host) | Backend login must strip backticks and host suffix | Backend `useAuth.js` / `auth.py` must parse accordingly (Req 1.3) |

---

## Summary

| Check | Result |
|-------|--------|
| sql/run_migrations.sh (7 files) | ✅ PASS |
| sql/run_migrations.sh (2nd run — idempotency) | ✅ PASS |
| pytest test_odbc_connection | ✅ PASS |
| pytest test_parameterized_query | ✅ PASS |
| run_role_tests.sh (7 assertions) | ✅ PASS |
| FACT_SALES partition count = 21 | ✅ PASS |
| EXPLAIN ANALYZE format recorded | ✅ DONE |
| Invisible index behaviour verified | ✅ DONE |
| REORGANIZE PARTITION verified | ✅ DONE |
| SHOW VIEW + EXPLAIN on view (no ERROR 1142) | ✅ PASS |
| CREATE TABLE IF NOT EXISTS idempotency | ✅ PASS |
| CURRENT_ROLE() format recorded | ✅ DONE |
| sp_insert_qe ODBC stub callable | ✅ PASS |
| GRANT statements only in 07_grants.sql | ✅ PASS |

**Stage 1 gate: PASSED.** Ready to proceed to Stage 2 (ETL Pipeline).
