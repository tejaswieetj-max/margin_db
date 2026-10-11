# Stage 2 Gate: Sequential-Scan Verification Report

**Date**: 2026-10-11  
**Database**: `margin_db` (MySQL 8.4)  
**Status**: PASSED  
**Requirements**: 4.11, 22.1, 22.2  

---

## 1. ETL Pipeline Execution & Verification

The real-data ETL pipeline was executed with strict zero-synthetic-data compliance. Synthetic sales generators and order placement scripts were completely omitted.

### Execution Sequence:
1. `etl/fetch_edgar.py` — Fetched 10-K and 10-Q filings from SEC EDGAR API, derived Q4 metrics, validated totals against reported annual figures.
2. `etl/load_financials.py` — Loaded 16 fiscal quarters (FY22Q1–FY25Q4) across Microsoft's 3 official segments (Productivity and Business Processes, Intelligent Cloud, More Personal Computing).
3. `etl/load_segments_gaming.py` — Seeded `GAMING_REVENUE_SERIES` (16 quarters) and linked product line mapping in `USER_SEGMENT_MAP`.
4. `etl/load_steam.py` — Loaded Steam concurrent player metrics for Microsoft/Bethesda/Activision titles into `STEAM_PLAYER`.
5. `etl/load_trends.py` — Loaded Google Trends search volume time series into `GOOGLE_TREND`.
6. `etl/load_events.py` — Loaded curated business events (game launches, price changes, acquisitions) into `BUSINESS_EVENT`.
7. `etl/compute_event_windows.py` — Calculated monthly pre/post baseline windows and lift ratios in `EVENT_WINDOW_MONTH`.
8. `etl/load_azure_vm.py` — Batch-loaded the real Azure Public Dataset V2 VM trace into partitioned `AZURE_VM` table with size progress snapshots.
9. `etl/load_sales.sh` — Master orchestrator validating row count reconciliation and stage gates.

---

## 2. Table Row Count Reconciliations

| Table Name | Actual Rows | Expected Rows | Status |
|:---|:---:|:---:|:---:|
| `AZURE_VM` | **2,695,548** | 2,695,548 | **PASS** (Exact match) |
| `QUARTERLY_FINANCIALS` | **48** | 48 (16 quarters × 3 segments) | **PASS** |
| `COMPANY_SEGMENT` | **3** | 3 | **PASS** |
| `GAMING_REVENUE_SERIES` | **16** | 16 | **PASS** |
| `STEAM_PLAYER` | **229** | 229 | **PASS** |
| `GOOGLE_TREND` | **786** | 786 | **PASS** |
| `BUSINESS_EVENT` | **9** | 9 | **PASS** |
| `EVENT_WINDOW_MONTH` | **21** | 21 | **PASS** |
| `DB_SIZE_SNAPSHOT` | **7** | ≥ 6 batch snapshots | **PASS** |
| `FACT_SALES` | **0** | 0 (Synthetic table unused) | **PASS** |
| `ORDERS` | **0** | 0 (Synthetic table unused) | **PASS** |
| `ORDER_ITEM` | **0** | 0 (Synthetic table unused) | **PASS** |

---

## 3. Real-Time Metadata Verification (`information_schema_stats_expiry`)

Session setting verified:
```sql
SET SESSION information_schema_stats_expiry = 0;
```
Result from `information_schema.TABLES` for `AZURE_VM`:
- `TABLE_ROWS`: 2,788,690 (InnoDB statistical sample)
- `DATA_LENGTH`: 1,341,014,016 bytes (1,278.90 MB)
- `INDEX_LENGTH`: 0 bytes (no secondary indexes exist)

Immediate effect confirmed: with `information_schema_stats_expiry = 0`, MySQL bypasses the default 24-hour cache and queries InnoDB table metadata dynamically.

---

## 4. Heavy_Demo_Query Benchmark & EXPLAIN Analysis

### Query Specification:
Executed via MySQL CLI by authorized user `engineer_demo` (`db_engineer` role):
```sql
SET SESSION information_schema_stats_expiry = 0;

SELECT
    vm_category,
    vcpu_bucket,
    COUNT(*)                     AS vm_count,
    ROUND(AVG(avg_cpu), 4)       AS mean_avg_cpu,
    ROUND(AVG(max_cpu), 4)       AS mean_max_cpu,
    ROUND(AVG(p95_max_cpu), 4)   AS mean_p95_cpu
FROM AZURE_VM
WHERE vm_category = 'Delay-insensitive'
  AND vcpu_bucket = '8'
  AND created_at BETWEEN 500000 AND 2000000
GROUP BY
    vm_category,
    vcpu_bucket;
```

### Baseline (Full Table Scan — No Secondary Index):
```text
-> Group aggregate: avg(azure_vm.p95_max_cpu), avg(azure_vm.max_cpu), avg(azure_vm.avg_cpu), count(0)  (cost=361432 rows=1)
    -> Filter: ((azure_vm.vm_category = 'Delay-insensitive') and (azure_vm.vcpu_bucket = '8') and (azure_vm.created_at between 500000 and 2000000))  (cost=360718 rows=3098)
        -> Table scan on AZURE_VM  (cost=360718 rows=2.79e+6)
```
- **Execution Time (Cold)**: **6.952 seconds** (Requirement: > 2.0s — **PASSED**)
- **Rows Examined**: 2,695,548 rows across all 7 partitions.

### Candidate Index Optimization:
```sql
CREATE INDEX idx_stage2_test ON AZURE_VM (vm_category, vcpu_bucket, created_at) ALGORITHM=INPLACE;
```
Creation time: 3.88s.

### With Index (`idx_stage2_test` Range Scan):
```text
-> Group aggregate: avg(azure_vm.p95_max_cpu), avg(azure_vm.max_cpu), avg(azure_vm.avg_cpu), count(0)  (cost=1554 rows=1)
    -> Index range scan on AZURE_VM using idx_stage2_test over (vm_category = 'Delay-insensitive' AND vcpu_bucket = '8' AND 500000 <= created_at <= 2000000), with index condition: ((azure_vm.vm_category = 'Delay-insensitive') and (azure_vm.vcpu_bucket = '8') and (azure_vm.created_at between 500000 and 2000000))  (cost=1304 rows=1086)
```
- **Execution Time**: **0.005 seconds**
- **Speedup**: **99.9%** (Requirement: ≥ 50% speedup — **PASSED**)

### Clean-Up:
```sql
DROP INDEX idx_stage2_test ON AZURE_VM;
```
Verified: `SHOW INDEX FROM AZURE_VM` confirms only the primary key `(vm_id, trace_day)` remains. No secondary index is left on the table.

---

## 5. Gate Conclusion
Stage 2 Gate verification criteria are 100% satisfied:
- Full dataset (2,695,548 rows) loaded and verified.
- Full table scan confirmed via `EXPLAIN FORMAT=TREE` with cold duration > 2.0s.
- Candidate index yields > 50% speedup (achieved 99.9%).
- Candidate index dropped clean.
