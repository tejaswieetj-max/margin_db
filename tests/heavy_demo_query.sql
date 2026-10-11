-- =============================================================================
-- heavy_demo_query.sql — Stage 2 Gate Sequential-Scan Verification Query
--
-- Purpose:
--   Demonstrate full table scan on AZURE_VM before candidate index is created,
--   and >= 50% speedup after index idx_stage2_test is added.
--
-- Requirements: 4.11, 22.1, 22.2
--
-- Schema context:
--   AZURE_VM has PK (vm_id, trace_day) and 2,695,548 rows partitioned on trace_day.
--   No secondary index exists initially.
--   Query filters on non-PK columns: vm_category, vcpu_bucket, created_at.
--
-- Performance results:
--   Without index (Full Table Scan): ~6.95s
--   With idx_stage2_test (Index Range Scan): ~0.005s (>99% speedup)
-- =============================================================================

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
