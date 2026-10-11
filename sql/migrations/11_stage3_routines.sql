-- =============================================================================
-- 11_stage3_routines.sql  Stage 3: Full stored procedure implementations
-- Replaces stub bodies from 04_routines.sql with production logic.
-- Requirements: 5.1, 5.2, 7.3, 7.5, 7.6, 7.9, 12.1, 12.2, 15.1, 17.1, 17.2, 17.3
--
-- SAFE to apply on an existing schema: DROP PROCEDURE IF EXISTS + CREATE.
-- No destructive table operations.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 3.1  sp_snapshot_sizes
--      Records current database size into DB_SIZE_SNAPSHOT.
--      Called by scheduled event ev_snapshot_sizes daily.
--      Requirements: 17.1, 17.2, 17.3
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_snapshot_sizes;
DELIMITER //
CREATE PROCEDURE sp_snapshot_sizes()
SQL SECURITY DEFINER
BEGIN
    SET SESSION information_schema_stats_expiry = 0;

    INSERT INTO DB_SIZE_SNAPSHOT (snapped_at, total_data_mb, total_index_mb, is_synthetic)
    SELECT
        CURDATE(),
        ROUND(SUM(data_length)  / 1048576.0, 2),
        ROUND(SUM(index_length) / 1048576.0, 2),
        FALSE
    FROM information_schema.TABLES
    WHERE table_schema = DATABASE()
    ON DUPLICATE KEY UPDATE
        total_data_mb  = VALUES(total_data_mb),
        total_index_mb = VALUES(total_index_mb);
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- 3.2  sp_margin_decomposition
--      Two-component decomposition of operating income change between two periods.
--      Components: revenue effect + margin-rate effect.
--      Access control: caller must have segment_id in USER_SEGMENT_MAP.
--      Requirements: 15.1, 5.1, 5.2, 5.10
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
    DECLARE v_fy_a   SMALLINT;
    DECLARE v_fq_a   TINYINT;
    DECLARE v_fy_b   SMALLINT;
    DECLARE v_fq_b   TINYINT;
    DECLARE v_seg_id INT DEFAULT NULL;

    -- Parse 'FY24Q2' -> fy=2024, fq=2
    SET v_fy_a = 2000 + CAST(SUBSTRING(p_period_a, 3, 2) AS UNSIGNED);
    SET v_fq_a =        CAST(SUBSTRING(p_period_a, 6, 1) AS UNSIGNED);
    SET v_fy_b = 2000 + CAST(SUBSTRING(p_period_b, 3, 2) AS UNSIGNED);
    SET v_fq_b =        CAST(SUBSTRING(p_period_b, 6, 1) AS UNSIGNED);

    -- Resolve segment with caller-permission check via USER_SEGMENT_MAP
    SELECT cs.segment_id INTO v_seg_id
    FROM   COMPANY_SEGMENT cs
    JOIN   USER_SEGMENT_MAP usm
           ON usm.segment_id = cs.segment_id
          AND usm.db_user = SUBSTRING_INDEX(USER(), '@', 1)
    WHERE  cs.name = p_segment
      AND  (usm.product_line IS NULL)
    LIMIT  1;

    IF v_seg_id IS NULL THEN
        -- No permission or segment not found: return empty result set
        SELECT NULL AS component, NULL AS value_delta WHERE FALSE;
    ELSE
        SELECT component, value_delta
        FROM (
            -- Revenue effect = delta_revenue * period_A_margin_rate
            SELECT
                'revenue_effect' AS component,
                ROUND(
                    (qfb.revenue - qfa.revenue)
                    * (qfa.operating_income / NULLIF(qfa.revenue, 0)),
                4) AS value_delta
            FROM QUARTERLY_FINANCIALS qfa
            JOIN QUARTERLY_FINANCIALS qfb
              ON qfb.segment_id     = qfa.segment_id
             AND qfb.fiscal_year    = v_fy_b
             AND qfb.fiscal_quarter = v_fq_b
            WHERE qfa.segment_id     = v_seg_id
              AND qfa.fiscal_year    = v_fy_a
              AND qfa.fiscal_quarter = v_fq_a

            UNION ALL

            -- Margin-rate effect = period_B_revenue * delta_margin_rate
            SELECT
                'margin_rate_effect' AS component,
                ROUND(
                    qfb.revenue
                    * (  (qfb.operating_income / NULLIF(qfb.revenue, 0))
                       - (qfa.operating_income / NULLIF(qfa.revenue, 0)) ),
                4) AS value_delta
            FROM QUARTERLY_FINANCIALS qfa
            JOIN QUARTERLY_FINANCIALS qfb
              ON qfb.segment_id     = qfa.segment_id
             AND qfb.fiscal_year    = v_fy_b
             AND qfb.fiscal_quarter = v_fq_b
            WHERE qfa.segment_id     = v_seg_id
              AND qfa.fiscal_year    = v_fy_a
              AND qfa.fiscal_quarter = v_fq_a
        ) decomp;
    END IF;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- 3.4  sp_update_pricing_rule
--      Optimistic-lock update of PRICING_RULE.
--      On version mismatch: INSERT CONFLICT_LOG, OUT p_status='CONFLICT' (no SIGNAL).
--      Requirements: 12.1, 12.2
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
    UPDATE PRICING_RULE
       SET price_value = p_new_value,
           version     = version + 1
     WHERE rule_id = p_rule_id
       AND version  = p_expected_version;

    IF ROW_COUNT() = 1 THEN
        SET p_status = 'OK';
    ELSE
        INSERT INTO CONFLICT_LOG (
            rule_id,
            attempted_by,
            attempted_at,
            attempted_value,
            expected_version
        ) VALUES (
            p_rule_id,
            SUBSTRING_INDEX(USER(), '@', 1),
            NOW(3),
            p_new_value,
            p_expected_version
        );
        SET p_status = 'CONFLICT';
    END IF;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- 3.10a  sp_insert_qe
--         Insert a QUERY_EXECUTION row; return new PK via OUT parameter.
--         Requirements: 7.3
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
    INSERT INTO QUERY_EXECUTION (
        db_user, digest_text, started_at, duration_ms, rows_examined, rows_sent
    ) VALUES (
        p_db_user, p_digest_text, p_started_at, p_duration_ms, p_rows_examined, p_rows_sent
    );
    SET p_qe_id = LAST_INSERT_ID();
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- 3.10b  sp_insert_qpn
--         Insert a QUERY_PLAN_NODE row.
--         Requirements: 7.3
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
    INSERT INTO QUERY_PLAN_NODE (
        qe_id, node_id, parent_node_id, operation, table_name,
        pct_time, est_rows, actual_rows
    ) VALUES (
        p_qe_id, p_node_id, p_parent_node_id, p_operation, p_table_name,
        p_pct_time, p_est_rows, p_actual_rows
    );
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- 3.6b  sp_record_index_test
--        Record before/after query timing for an index recommendation.
--        Requirements: 7.6, 7.7
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
    UPDATE INDEX_RECOMMENDATION
       SET before_ms = p_before_ms,
           after_ms  = p_after_ms
     WHERE rec_id = p_rec_id;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- 3.6c  sp_flag_query
--        Mark a QUERY_EXECUTION row as flagged for index recommendation.
--        Requirements: 7.5, 7.9
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_flag_query;
DELIMITER //
CREATE PROCEDURE sp_flag_query(
    IN p_qe_id BIGINT
)
SQL SECURITY DEFINER
BEGIN
    UPDATE QUERY_EXECUTION
       SET flagged = 1
     WHERE qe_id = p_qe_id;
END //
DELIMITER ;
