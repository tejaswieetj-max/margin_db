-- =============================================================================
-- Migration 10a: Create AZURE_VM table
--
-- Azure Public Dataset V2 VM trace (real, Microsoft-published workload data)
-- source_url: TODO
-- licence: TODO
--
-- 2,695,548 rows, 11 columns, trace-relative seconds, trace_day = FLOOR(created_at / 86400)
-- RANGE partitioned on trace_day (part of composite PK (vm_id, trace_day))
-- No foreign keys. Guarded and idempotent.
-- =============================================================================

CREATE TABLE IF NOT EXISTS AZURE_VM (
    vm_id           VARCHAR(64)       NOT NULL,
    trace_day       TINYINT UNSIGNED  NOT NULL,
    subscription_id VARCHAR(64)       NOT NULL,
    deployment_id   VARCHAR(128)      NOT NULL,
    created_at      INT UNSIGNED      NOT NULL,
    deleted_at      INT UNSIGNED      NOT NULL,
    max_cpu         DOUBLE            NOT NULL,
    avg_cpu         DOUBLE            NOT NULL,
    p95_max_cpu     DOUBLE            NOT NULL,
    vm_category     VARCHAR(30)       NOT NULL,
    vcpu_bucket     VARCHAR(10)       NOT NULL,
    memory_bucket   VARCHAR(10)       NOT NULL,
    PRIMARY KEY (vm_id, trace_day)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
PARTITION BY RANGE (trace_day) (
    PARTITION p_day0_4   VALUES LESS THAN (5),
    PARTITION p_day5_9   VALUES LESS THAN (10),
    PARTITION p_day10_14 VALUES LESS THAN (15),
    PARTITION p_day15_19 VALUES LESS THAN (20),
    PARTITION p_day20_24 VALUES LESS THAN (25),
    PARTITION p_day25_29 VALUES LESS THAN (30),
    PARTITION p_catch    VALUES LESS THAN MAXVALUE
);
