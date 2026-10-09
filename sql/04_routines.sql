-- =============================================================================
-- 04_routines.sql — Stored procedure stubs for Margin-DB
-- Stage 1: Correct signatures with minimal stub bodies.
-- Full logic is added in Stage 3.
--
-- IMPORTANT: MySQL syntax only. No Oracle PL/SQL constructs.
-- No := operator, no DBMS_* packages, no DECLARE...EXCEPTION blocks,
-- no RAISE_APPLICATION_ERROR, no bare EXCEPTION keyword.
-- Error handling (Stage 3) uses: DECLARE ... HANDLER FOR SQLEXCEPTION BEGIN ... END
--
-- All procedures use SQL SECURITY DEFINER and follow the
-- DROP PROCEDURE IF EXISTS + CREATE PROCEDURE pattern (Requirements 5.1, 5.2, 3.8).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- sp_margin_decomposition
-- Business analyst procedure: margin breakdown between two periods.
-- Grants: business_analyst (07_grants.sql)
-- Requirements: 2.2, 5.2
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_margin_decomposition;
DELIMITER //
CREATE PROCEDURE sp_margin_decomposition(
    IN p_segment  VARCHAR(50),
    IN p_period_a VARCHAR(10),
    IN p_period_b VARCHAR(10)
)
SQL SECURITY DEFINER
BEGIN
    SELECT NULL;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- sp_forecast_business
-- Business analyst procedure: revenue forecast for a game-launch event.
-- Grants: business_analyst (07_grants.sql)
-- Requirements: 2.2, 5.2, 8.1
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_forecast_business;
DELIMITER //
CREATE PROCEDURE sp_forecast_business(
    IN p_event_id INT UNSIGNED
)
SQL SECURITY DEFINER
BEGIN
    SELECT NULL;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- sp_update_pricing_rule
-- Business analyst procedure: optimistic-lock update of a pricing rule.
-- Grants: business_analyst (07_grants.sql)
-- Requirements: 2.2, 5.2
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_update_pricing_rule;
DELIMITER //
CREATE PROCEDURE sp_update_pricing_rule(
    IN  p_rule_id          INT,
    IN  p_new_value        DECIMAL(10,4),
    IN  p_expected_version INT,
    OUT p_status           VARCHAR(20)
)
SQL SECURITY DEFINER
BEGIN
    SET p_status = NULL;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- sp_place_order
-- Business analyst procedure: insert an order with line items from JSON.
-- Grants: business_analyst (07_grants.sql)
-- Requirements: 2.2, 5.2
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_place_order;
DELIMITER //
CREATE PROCEDURE sp_place_order(
    IN p_order_date DATE,
    IN p_device_id  INT,
    IN p_region_id  INT,
    IN p_channel_id INT,
    IN p_items      JSON
)
SQL SECURITY DEFINER
BEGIN
    SELECT NULL;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- sp_recommend_index
-- Telemetry procedure: create an invisible index recommendation.
-- Called by svc_telemetry after a flagged slow query.
-- Grants: role_telemetry (07_grants.sql)
-- Requirements: 2.6, 5.2, 7.6
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_recommend_index;
DELIMITER //
CREATE PROCEDURE sp_recommend_index(
    IN  p_qe_id       BIGINT,
    IN  p_table        VARCHAR(64),
    IN  p_columns_csv  VARCHAR(500),
    OUT p_rec_id       INT
)
SQL SECURITY DEFINER
BEGIN
    SET p_rec_id = NULL;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- sp_apply_index
-- DB engineer procedure: make a recommended index visible (approve).
-- Grants: db_engineer (07_grants.sql)
-- Requirements: 2.3, 5.2, 7.10
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_apply_index;
DELIMITER //
CREATE PROCEDURE sp_apply_index(
    IN p_rec_id INT
)
SQL SECURITY DEFINER
BEGIN
    SELECT NULL;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- sp_reject_index
-- DB engineer procedure: drop a recommended index (reject).
-- Grants: db_engineer (07_grants.sql)
-- Requirements: 2.3, 5.2, 7.11
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_reject_index;
DELIMITER //
CREATE PROCEDURE sp_reject_index(
    IN p_rec_id INT
)
SQL SECURITY DEFINER
BEGIN
    SELECT NULL;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- sp_add_partition
-- DB engineer procedure: add a new range partition to a table.
-- Grants: db_engineer (07_grants.sql)
-- Requirements: 2.3, 5.2
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_add_partition;
DELIMITER //
CREATE PROCEDURE sp_add_partition(
    IN p_table_name VARCHAR(64),
    IN p_boundary   DATE
)
SQL SECURITY DEFINER
BEGIN
    SELECT NULL;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- sp_snapshot_sizes
-- DB engineer procedure: record current database size into DB_SIZE_SNAPSHOT.
-- Called by scheduled event ev_snapshot_sizes daily.
-- Grants: db_engineer (07_grants.sql)
-- Requirements: 2.3, 5.2, 5.3
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_snapshot_sizes;
DELIMITER //
CREATE PROCEDURE sp_snapshot_sizes()
SQL SECURITY DEFINER
BEGIN
    SELECT NULL;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- sp_forecast_database
-- DB engineer procedure: compute DB load forecast for an event.
-- Called by scheduled event ev_recalc_forecasts nightly.
-- Grants: db_engineer (07_grants.sql)
-- Requirements: 2.3, 5.2, 9.1
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_forecast_database;
DELIMITER //
CREATE PROCEDURE sp_forecast_database(
    IN p_event_id INT UNSIGNED
)
SQL SECURITY DEFINER
BEGIN
    SELECT NULL;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- sp_insert_qe
-- Telemetry procedure: insert a QUERY_EXECUTION row, return new PK.
-- Called by svc_telemetry via the backend Telemetry_Service.
-- Grants: role_telemetry (07_grants.sql)
-- Requirements: 2.6, 5.2, 7.3
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_insert_qe;
DELIMITER //
CREATE PROCEDURE sp_insert_qe(
    IN  p_db_user        VARCHAR(64),
    IN  p_digest_text    TEXT,
    IN  p_started_at     DATETIME(3),
    IN  p_duration_ms    DECIMAL(12,3),
    IN  p_rows_examined  INT UNSIGNED,
    IN  p_rows_sent      INT UNSIGNED,
    OUT p_qe_id          BIGINT UNSIGNED
)
SQL SECURITY DEFINER
BEGIN
    SET p_qe_id = NULL;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- sp_insert_qpn
-- Telemetry procedure: insert a QUERY_PLAN_NODE row.
-- Called by svc_telemetry after EXPLAIN ANALYZE parsing.
-- Grants: role_telemetry (07_grants.sql)
-- Requirements: 2.6, 5.2, 7.3
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_insert_qpn;
DELIMITER //
CREATE PROCEDURE sp_insert_qpn(
    IN p_qe_id          BIGINT,
    IN p_node_id        SMALLINT,
    IN p_parent_node_id SMALLINT,
    IN p_operation      VARCHAR(200),
    IN p_table_name     VARCHAR(64),
    IN p_pct_time       DECIMAL(6,2),
    IN p_est_rows       BIGINT,
    IN p_actual_rows    BIGINT
)
SQL SECURITY DEFINER
BEGIN
    SELECT NULL;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- sp_record_index_test
-- Telemetry procedure: record before/after timing for an index test.
-- Called by the FastAPI background task after index recommendation.
-- Grants: role_telemetry (07_grants.sql)
-- Requirements: 2.6, 5.2, 7.7
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_record_index_test;
DELIMITER //
CREATE PROCEDURE sp_record_index_test(
    IN p_rec_id    INT,
    IN p_before_ms DECIMAL(12,3),
    IN p_after_ms  DECIMAL(12,3)
)
SQL SECURITY DEFINER
BEGIN
    SELECT NULL;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- sp_flag_query
-- Telemetry procedure: mark a QUERY_EXECUTION row as flagged.
-- Called by telemetry.py via the svc_telemetry connection.
-- Grants: role_telemetry (07_grants.sql)
-- Requirements: 2.6, 5.2, 7.9
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_flag_query;
DELIMITER //
CREATE PROCEDURE sp_flag_query(
    IN p_qe_id BIGINT
)
SQL SECURITY DEFINER
BEGIN
    SELECT NULL;
END //
DELIMITER ;
