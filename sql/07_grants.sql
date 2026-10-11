-- =============================================================================
-- 07_grants.sql
-- ALL GRANT statements for margin_db.
-- This is the ONLY migration file that contains GRANT statements.
-- Referenced requirements: 2.1, 2.2, 2.3, 2.4, 2.6, 2.8, 2.9, 3.9
-- =============================================================================

-- ---------------------------------------------------------------------------
-- ROLE: business_analyst
-- Req 2.1 — SELECT on analyst views
-- Req 2.2 — EXECUTE on analyst stored procedures
-- Req 2.4 — SHOW VIEW on all v_* views
-- ---------------------------------------------------------------------------

-- SELECT on views exposed to analysts
GRANT SELECT ON margin_db.v_sales            TO 'business_analyst';
GRANT SELECT ON margin_db.v_financials       TO 'business_analyst';
GRANT SELECT ON margin_db.v_events           TO 'business_analyst';
GRANT SELECT ON margin_db.v_forecast_business TO 'business_analyst';
GRANT SELECT ON margin_db.v_my_queries       TO 'business_analyst';
GRANT SELECT ON margin_db.v_vm_workload      TO 'business_analyst';
GRANT SELECT ON margin_db.AZURE_VM           TO 'business_analyst';

-- EXECUTE on analyst stored procedures
GRANT EXECUTE ON PROCEDURE margin_db.sp_margin_decomposition TO 'business_analyst';
GRANT EXECUTE ON PROCEDURE margin_db.sp_forecast_business    TO 'business_analyst';
GRANT EXECUTE ON PROCEDURE margin_db.sp_update_pricing_rule  TO 'business_analyst';
GRANT EXECUTE ON PROCEDURE margin_db.sp_place_order          TO 'business_analyst';

-- SHOW VIEW on all v_* views (lets analysts inspect view definitions)
GRANT SHOW VIEW ON margin_db.v_sales             TO 'business_analyst';
GRANT SHOW VIEW ON margin_db.v_financials        TO 'business_analyst';
GRANT SHOW VIEW ON margin_db.v_events            TO 'business_analyst';
GRANT SHOW VIEW ON margin_db.v_forecast_business TO 'business_analyst';
GRANT SHOW VIEW ON margin_db.v_my_queries        TO 'business_analyst';
GRANT SHOW VIEW ON margin_db.v_events_min        TO 'business_analyst';
GRANT SHOW VIEW ON margin_db.v_forecast_database TO 'business_analyst';
GRANT SHOW VIEW ON margin_db.v_partitions        TO 'business_analyst';
GRANT SHOW VIEW ON margin_db.v_vm_workload       TO 'business_analyst';

-- ---------------------------------------------------------------------------
-- ROLE: db_engineer
-- Req 2.3 — SELECT on intelligence tables, views, performance_schema;
--           EXECUTE on maintenance procedures; SHOW VIEW on all v_* views
-- Note: information_schema is readable by all authenticated MySQL users —
--       no explicit GRANT is needed.
-- ---------------------------------------------------------------------------

-- SELECT on intelligence/monitoring tables and real workload table
GRANT SELECT ON margin_db.QUERY_EXECUTION      TO 'db_engineer';
GRANT SELECT ON margin_db.QUERY_PLAN_NODE      TO 'db_engineer';
GRANT SELECT ON margin_db.INDEX_RECOMMENDATION TO 'db_engineer';
GRANT SELECT ON margin_db.DB_SIZE_SNAPSHOT     TO 'db_engineer';
GRANT SELECT ON margin_db.CONFLICT_LOG         TO 'db_engineer';
GRANT SELECT ON margin_db.AZURE_VM             TO 'db_engineer';

-- SELECT on engineer views
GRANT SELECT ON margin_db.v_events_min        TO 'db_engineer';
GRANT SELECT ON margin_db.v_forecast_database TO 'db_engineer';
GRANT SELECT ON margin_db.v_partitions        TO 'db_engineer';
GRANT SELECT ON margin_db.v_vm_workload       TO 'db_engineer';
GRANT SHOW VIEW ON margin_db.v_vm_workload    TO 'db_engineer';

-- SELECT on performance_schema (global privilege)
GRANT SELECT ON performance_schema.* TO 'db_engineer';

-- EXECUTE on maintenance/engineering stored procedures
GRANT EXECUTE ON PROCEDURE margin_db.sp_apply_index       TO 'db_engineer';
GRANT EXECUTE ON PROCEDURE margin_db.sp_reject_index      TO 'db_engineer';
GRANT EXECUTE ON PROCEDURE margin_db.sp_add_partition     TO 'db_engineer';
GRANT EXECUTE ON PROCEDURE margin_db.sp_snapshot_sizes    TO 'db_engineer';
GRANT EXECUTE ON PROCEDURE margin_db.sp_forecast_database TO 'db_engineer';

-- SHOW VIEW on all v_* views
GRANT SHOW VIEW ON margin_db.v_sales             TO 'db_engineer';
GRANT SHOW VIEW ON margin_db.v_financials        TO 'db_engineer';
GRANT SHOW VIEW ON margin_db.v_events            TO 'db_engineer';
GRANT SHOW VIEW ON margin_db.v_forecast_business TO 'db_engineer';
GRANT SHOW VIEW ON margin_db.v_my_queries        TO 'db_engineer';
GRANT SHOW VIEW ON margin_db.v_events_min        TO 'db_engineer';
GRANT SHOW VIEW ON margin_db.v_forecast_database TO 'db_engineer';
GRANT SHOW VIEW ON margin_db.v_partitions        TO 'db_engineer';

-- ---------------------------------------------------------------------------
-- ROLE: role_telemetry
-- Req 2.6 — INSERT/UPDATE on telemetry tables; EXECUTE on telemetry procedures
-- ---------------------------------------------------------------------------

-- INSERT and UPDATE on telemetry write tables
GRANT INSERT, UPDATE ON margin_db.QUERY_EXECUTION      TO 'role_telemetry';
GRANT INSERT, UPDATE ON margin_db.QUERY_PLAN_NODE      TO 'role_telemetry';
GRANT INSERT, UPDATE ON margin_db.INDEX_RECOMMENDATION TO 'role_telemetry';

-- EXECUTE on telemetry stored procedures
GRANT EXECUTE ON PROCEDURE margin_db.sp_recommend_index   TO 'role_telemetry';
GRANT EXECUTE ON PROCEDURE margin_db.sp_insert_qe         TO 'role_telemetry';
GRANT EXECUTE ON PROCEDURE margin_db.sp_insert_qpn        TO 'role_telemetry';
GRANT EXECUTE ON PROCEDURE margin_db.sp_record_index_test TO 'role_telemetry';
GRANT EXECUTE ON PROCEDURE margin_db.sp_flag_query        TO 'role_telemetry';

-- ---------------------------------------------------------------------------
-- GRANT roles to user accounts
-- Req 2.8 — GRANT <role> TO <user>
-- Req 1.8 — analyst_demo (Gaming only), analyst_all (all segments), engineer_demo
-- ---------------------------------------------------------------------------

GRANT 'business_analyst' TO 'analyst_demo'@'localhost';
GRANT 'business_analyst' TO 'analyst_all'@'localhost';
GRANT 'db_engineer'      TO 'engineer_demo'@'localhost';
GRANT 'role_telemetry'   TO 'svc_telemetry'@'localhost';

-- ---------------------------------------------------------------------------
-- SET DEFAULT ROLE for all demo and service accounts
-- Req 2.9 — SET DEFAULT ROLE ALL TO <user>
-- ---------------------------------------------------------------------------

SET DEFAULT ROLE ALL TO
  'analyst_demo'@'localhost',
  'analyst_all'@'localhost',
  'engineer_demo'@'localhost',
  'svc_telemetry'@'localhost';
