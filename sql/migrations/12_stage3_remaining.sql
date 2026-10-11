-- =============================================================================
-- Migration 12: Stage 3 Stored Routines & Access Control
-- =============================================================================

-- Ensure required CONFIG keys exist
INSERT IGNORE INTO CONFIG (config_key, config_value) VALUES ('STEAM_TO_REVENUE_SCALE', '1.0');
INSERT IGNORE INTO CONFIG (config_key, config_value) VALUES ('AT_RISK_FACTOR', '1.25');
INSERT IGNORE INTO CONFIG (config_key, config_value) VALUES ('DB_ALLOCATED_MB', '10240');
INSERT IGNORE INTO CONFIG (config_key, config_value) VALUES ('BASELINE_MONTHS', '3');
INSERT IGNORE INTO CONFIG (config_key, config_value) VALUES ('MIN_BASELINE_MONTHS', '3');
INSERT IGNORE INTO CONFIG (config_key, config_value) VALUES ('EVENT_PRE_MONTHS', '3');
INSERT IGNORE INTO CONFIG (config_key, config_value) VALUES ('EVENT_POST_MONTHS', '3');
INSERT IGNORE INTO CONFIG (config_key, config_value) VALUES ('FORECAST_LIFT_MONTHS', '3');

-- ---------------------------------------------------------------------------
-- 1. sp_forecast_business
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_forecast_business;
DELIMITER //
CREATE PROCEDURE sp_forecast_business(
    IN p_event_id INT UNSIGNED
)
SQL SECURITY DEFINER
proc_main: BEGIN
    DECLARE v_pre_months INT DEFAULT 3;
    DECLARE v_post_months INT DEFAULT 3;
    DECLARE v_baseline_months INT DEFAULT 3;
    DECLARE v_min_baseline_months INT DEFAULT 3;
    DECLARE v_forecast_lift_months INT DEFAULT 3;
    DECLARE v_steam_scale DECIMAL(10,4) DEFAULT 1.0;

    DECLARE v_event_type VARCHAR(30);
    DECLARE v_event_name VARCHAR(200);
    DECLARE v_start_date DATE;
    DECLARE v_end_date DATE;
    DECLARE v_expected_lift DECIMAL(10,4);
    DECLARE v_steam_app_id INT;

    DECLARE v_measured_lift DECIMAL(10,4) DEFAULT NULL;
    DECLARE v_derived_lift DECIMAL(10,4) DEFAULT NULL;
    DECLARE v_n_prior_launches INT DEFAULT 0;
    DECLARE v_event_lift DECIMAL(10,4) DEFAULT 0.0;
    DECLARE v_lift_source VARCHAR(40);
    DECLARE v_confidence VARCHAR(20);

    DECLARE v_as_of_date DATE;
    DECLARE v_baseline_monthly_revenue DECIMAL(20,4) DEFAULT 0.0;
    DECLARE v_event_duration_months DECIMAL(10,4) DEFAULT 1.0;
    DECLARE v_projected_impact DECIMAL(20,4) DEFAULT 0.0;

    DECLARE v_forecast_id INT;
    DECLARE v_meas_count INT DEFAULT 0;

    -- Security / Access Control: Caller must have segment_id=3 (MPC) in USER_SEGMENT_MAP
    IF NOT EXISTS (
        SELECT 1 FROM USER_SEGMENT_MAP
        WHERE db_user = SUBSTRING_INDEX(USER(),'@',1)
          AND segment_id = 3
    ) THEN
        SELECT NULL AS forecast_id, NULL AS metric_name, NULL AS metric_value, NULL AS error_indicator WHERE FALSE;
        LEAVE proc_main;
    END IF;

    -- Read CONFIG keys
    SELECT CAST(config_value AS SIGNED) INTO v_pre_months FROM CONFIG WHERE config_key = 'EVENT_PRE_MONTHS';
    SELECT CAST(config_value AS SIGNED) INTO v_post_months FROM CONFIG WHERE config_key = 'EVENT_POST_MONTHS';
    SELECT CAST(config_value AS SIGNED) INTO v_baseline_months FROM CONFIG WHERE config_key = 'BASELINE_MONTHS';
    SELECT CAST(config_value AS SIGNED) INTO v_min_baseline_months FROM CONFIG WHERE config_key = 'MIN_BASELINE_MONTHS';
    SELECT CAST(config_value AS SIGNED) INTO v_forecast_lift_months FROM CONFIG WHERE config_key = 'FORECAST_LIFT_MONTHS';
    SELECT CAST(config_value AS DECIMAL(10,4)) INTO v_steam_scale FROM CONFIG WHERE config_key = 'STEAM_TO_REVENUE_SCALE';
    IF v_forecast_lift_months IS NULL THEN SET v_forecast_lift_months = 3; END IF;
    IF v_steam_scale IS NULL THEN SET v_steam_scale = 1.0; END IF;

    -- Look up event
    SELECT event_type, name, start_date, end_date, expected_lift, steam_app_id
    INTO v_event_type, v_event_name, v_start_date, v_end_date, v_expected_lift, v_steam_app_id
    FROM BUSINESS_EVENT
    WHERE event_id = p_event_id;

    IF v_event_type IS NULL THEN
        SELECT NULL AS forecast_id, NULL AS metric_name, NULL AS metric_value, NULL AS error_indicator WHERE FALSE;
        LEAVE proc_main;
    END IF;

    -- Duration in months
    IF v_end_date IS NOT NULL AND v_start_date IS NOT NULL AND v_end_date > v_start_date THEN
        SET v_event_duration_months = GREATEST(TIMESTAMPDIFF(MONTH, v_start_date, v_end_date) + 1, 1.0);
    ELSE
        SET v_event_duration_months = 1.0;
    END IF;

    -- Baseline: trailing 4-quarter average monthly revenue from QUARTERLY_FINANCIALS post-2023-10
    SELECT IFNULL(SUM(revenue) / 12.0, 0)
    INTO v_baseline_monthly_revenue
    FROM (
        SELECT revenue
        FROM QUARTERLY_FINANCIALS
        WHERE segment_id = 3
          AND ((fiscal_year = 2024 AND fiscal_quarter >= 2) OR (fiscal_year > 2024))
        ORDER BY fiscal_year DESC, fiscal_quarter DESC
        LIMIT 4
    ) t;

    -- Lift source selection in order of preference (a -> b -> c -> d)
    IF v_steam_app_id IS NOT NULL THEN
        SELECT AVG(lift_ratio) - 1.0, COUNT(*)
        INTO v_measured_lift, v_meas_count
        FROM EVENT_WINDOW_MONTH
        WHERE event_id = p_event_id
          AND month_offset BETWEEN 0 AND (v_forecast_lift_months - 1)
          AND lift_ratio IS NOT NULL;
    END IF;

    IF v_steam_app_id IS NOT NULL AND v_meas_count > 0 AND v_measured_lift IS NOT NULL THEN
        SET v_event_lift = v_measured_lift;
        SET v_lift_source = 'measured';
        SET v_confidence = 'high';
    ELSEIF v_event_type = 'game_launch' THEN
        SELECT
            AVG(sub.m_lift),
            COUNT(*)
        INTO v_derived_lift, v_n_prior_launches
        FROM (
            SELECT be.event_id, (AVG(ewm.lift_ratio) - 1.0) AS m_lift
            FROM BUSINESS_EVENT be
            JOIN EVENT_WINDOW_MONTH ewm ON be.event_id = ewm.event_id
            WHERE be.event_type = 'game_launch'
              AND be.event_id != p_event_id
              AND be.start_date < v_start_date
              AND ewm.month_offset BETWEEN 0 AND (v_forecast_lift_months - 1)
              AND ewm.lift_ratio IS NOT NULL
            GROUP BY be.event_id
            HAVING COUNT(ewm.lift_ratio) >= 1
        ) sub;

        IF v_n_prior_launches > 0 AND v_derived_lift IS NOT NULL THEN
            SET v_event_lift = v_derived_lift;
            SET v_lift_source = 'derived_from_prior_launches';
            SET v_confidence = 'medium';
        ELSEIF v_expected_lift IS NOT NULL THEN
            SET v_event_lift = v_expected_lift - 1.0;
            SET v_lift_source = 'expected_lift';
            SET v_confidence = 'medium';
        ELSE
            SET v_event_lift = 0.0;
            SET v_lift_source = 'zero_fallback';
            SET v_confidence = 'low';
        END IF;
    ELSEIF v_expected_lift IS NOT NULL THEN
        SET v_event_lift = v_expected_lift - 1.0;
        SET v_lift_source = 'expected_lift';
        SET v_confidence = 'medium';
    ELSE
        SET v_event_lift = 0.0;
        SET v_lift_source = 'zero_fallback';
        SET v_confidence = 'low';
    END IF;

    SET v_projected_impact = ROUND(v_baseline_monthly_revenue * v_event_duration_months * v_event_lift, 4);
    SET v_as_of_date = IFNULL(@as_of_date, CURDATE());

    INSERT INTO FORECAST (kind, event_id, as_of_date, created_at)
    VALUES ('business', p_event_id, v_as_of_date, NOW(3));
    SET v_forecast_id = LAST_INSERT_ID();

    INSERT INTO FORECAST_LINE (forecast_id, line_no, metric_name, metric_value, error_indicator)
    VALUES
        (v_forecast_id, 1, 'baseline_monthly_revenue', v_baseline_monthly_revenue, NULL),
        (v_forecast_id, 2, 'event_duration_months', v_event_duration_months, NULL),
        (v_forecast_id, 3, 'event_lift', v_event_lift, NULL),
        (v_forecast_id, 4, 'projected_revenue_impact', v_projected_impact, NULL),
        (v_forecast_id, 5, 'lift_source', NULL, v_lift_source),
        (v_forecast_id, 6, 'confidence_tier', NULL, v_confidence);

    IF v_lift_source = 'derived_from_prior_launches' THEN
        INSERT INTO FORECAST_LINE (forecast_id, line_no, metric_name, metric_value, error_indicator)
        VALUES (v_forecast_id, 7, 'n_prior_launches', v_n_prior_launches, NULL);
    END IF;

    INSERT INTO FORECAST_LINE (forecast_id, line_no, metric_name, metric_value, error_indicator)
    VALUES (v_forecast_id, 8, 'steam_to_revenue_scale', v_steam_scale, 'illustrative proxy');

    SELECT
        forecast_id,
        line_no,
        metric_name,
        metric_value,
        error_indicator
    FROM FORECAST_LINE
    WHERE forecast_id = v_forecast_id
    ORDER BY line_no;

END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- 2. sp_recommend_index
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_recommend_index;
DELIMITER //
CREATE PROCEDURE sp_recommend_index(
    IN  p_qe_id       BIGINT,
    IN  p_table       VARCHAR(64),
    IN  p_columns_csv VARCHAR(500),
    OUT p_rec_id      INT
)
SQL SECURITY DEFINER
proc_rec: BEGIN
    DECLARE v_existing_rec_id INT;
    DECLARE v_rec_id          INT;
    DECLARE v_cols_rem        VARCHAR(500);
    DECLARE v_comma_pos       INT;
    DECLARE v_curr_col        VARCHAR(64);
    DECLARE v_col_count       INT;

    -- 1. Deduplication check
    SELECT rec_id INTO v_existing_rec_id
    FROM INDEX_RECOMMENDATION
    WHERE table_name = p_table
      AND columns_csv = p_columns_csv
      AND status IN ('proposed', 'testing', 'applied')
    LIMIT 1;

    IF v_existing_rec_id IS NOT NULL THEN
        SET p_rec_id = v_existing_rec_id;
        LEAVE proc_rec;
    END IF;

    -- 2. Whitelist check (only AZURE_VM allowed)
    IF UPPER(TRIM(p_table)) != 'AZURE_VM' THEN
        SIGNAL SQLSTATE '45001'
        SET MESSAGE_TEXT = 'Table not in allowed list for index recommendation';
    END IF;

    -- 3. Column validation
    SET v_cols_rem = p_columns_csv;
    WHILE LENGTH(v_cols_rem) > 0 DO
        SET v_comma_pos = INSTR(v_cols_rem, ',');
        IF v_comma_pos > 0 THEN
            SET v_curr_col = TRIM(SUBSTRING(v_cols_rem, 1, v_comma_pos - 1));
            SET v_cols_rem = TRIM(SUBSTRING(v_cols_rem, v_comma_pos + 1));
        ELSE
            SET v_curr_col = TRIM(v_cols_rem);
            SET v_cols_rem = '';
        END IF;

        IF v_curr_col = '' OR v_curr_col NOT REGEXP '^[a-zA-Z0-9_]+$' THEN
            SIGNAL SQLSTATE '45002'
            SET MESSAGE_TEXT = 'Invalid column name format in columns_csv';
        END IF;

        SELECT COUNT(*) INTO v_col_count
        FROM information_schema.COLUMNS
        WHERE table_schema = DATABASE()
          AND table_name = p_table
          AND column_name = v_curr_col;

        IF v_col_count = 0 THEN
            SIGNAL SQLSTATE '45002'
            SET MESSAGE_TEXT = 'Column does not exist in target table';
        END IF;
    END WHILE;

    -- 4 & 5. Insert placeholder & capture ID
    INSERT INTO INDEX_RECOMMENDATION (qe_id, table_name, columns_csv, index_name, status)
    VALUES (p_qe_id, p_table, p_columns_csv, 'placeholder', 'testing');
    SET v_rec_id = LAST_INSERT_ID();
    SET p_rec_id = v_rec_id;

    -- 6. Update index name
    UPDATE INDEX_RECOMMENDATION
    SET index_name = CONCAT('idx_rec_', v_rec_id)
    WHERE rec_id = v_rec_id;

    -- 7. Dynamic ALTER TABLE ADD INDEX INVISIBLE
    SET @sql_idx = CONCAT('ALTER TABLE `', p_table, '` ADD INDEX `idx_rec_', v_rec_id, '` (', p_columns_csv, ') INVISIBLE, ALGORITHM=INPLACE');
    PREPARE stmt_idx FROM @sql_idx;
    EXECUTE stmt_idx;
    DEALLOCATE PREPARE stmt_idx;

END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- 3. sp_apply_index
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_apply_index;
DELIMITER //
CREATE PROCEDURE sp_apply_index(
    IN p_rec_id INT
)
SQL SECURITY DEFINER
BEGIN
    DECLARE v_status VARCHAR(20);
    DECLARE v_after_ms INT;
    DECLARE v_table VARCHAR(64);
    DECLARE v_index VARCHAR(64);

    SELECT status, after_ms, table_name, index_name
    INTO v_status, v_after_ms, v_table, v_index
    FROM INDEX_RECOMMENDATION
    WHERE rec_id = p_rec_id;

    IF v_status IS NULL THEN
        SELECT 'Recommendation not found' AS result;
    ELSEIF v_after_ms IS NULL THEN
        SELECT 'Index test not yet complete' AS result;
    ELSEIF v_status != 'testing' THEN
        SELECT CONCAT('Cannot apply index with status: ', v_status) AS result;
    ELSE
        SET @sql_vis = CONCAT('ALTER TABLE `', v_table, '` ALTER INDEX `', v_index, '` VISIBLE');
        PREPARE stmt_vis FROM @sql_vis;
        EXECUTE stmt_vis;
        DEALLOCATE PREPARE stmt_vis;

        UPDATE INDEX_RECOMMENDATION
        SET status = 'applied',
            decided_by = SUBSTRING_INDEX(USER(), '@', 1),
            decided_at = NOW(3)
        WHERE rec_id = p_rec_id;

        SELECT 'applied' AS result;
    END IF;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- 4. sp_reject_index
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_reject_index;
DELIMITER //
CREATE PROCEDURE sp_reject_index(
    IN p_rec_id INT
)
SQL SECURITY DEFINER
BEGIN
    DECLARE v_status VARCHAR(20);
    DECLARE v_table VARCHAR(64);
    DECLARE v_index VARCHAR(64);
    DECLARE CONTINUE HANDLER FOR SQLEXCEPTION
    BEGIN
        -- Ignore DDL error if index does not exist
    END;

    SELECT status, table_name, index_name
    INTO v_status, v_table, v_index
    FROM INDEX_RECOMMENDATION
    WHERE rec_id = p_rec_id;

    IF v_status IS NULL THEN
        SELECT 'Recommendation not found' AS result;
    ELSEIF v_status NOT IN ('testing', 'proposed') THEN
        SELECT CONCAT('Cannot reject index with status: ', v_status) AS result;
    ELSE
        SET @sql_drop = CONCAT('ALTER TABLE `', v_table, '` DROP INDEX `', v_index, '`');
        PREPARE stmt_drop FROM @sql_drop;
        EXECUTE stmt_drop;
        DEALLOCATE PREPARE stmt_drop;

        UPDATE INDEX_RECOMMENDATION
        SET status = 'rejected',
            decided_by = SUBSTRING_INDEX(USER(), '@', 1),
            decided_at = NOW(3)
        WHERE rec_id = p_rec_id;

        SELECT 'rejected' AS result;
    END IF;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- 5. sp_add_partition (Idempotent)
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_add_partition;
DELIMITER //
CREATE PROCEDURE sp_add_partition(
    IN p_table_name VARCHAR(64),
    IN p_boundary   DATE
)
SQL SECURITY DEFINER
BEGIN
    DECLARE v_part_name VARCHAR(30);
    DECLARE v_next_limit INT;
    DECLARE v_month INT;
    DECLARE v_fy INT;
    DECLARE v_fq INT;

    IF UPPER(TRIM(p_table_name)) != 'AZURE_VM' THEN
        SIGNAL SQLSTATE '45003'
        SET MESSAGE_TEXT = 'Table not allowed for partitioning: only AZURE_VM permitted';
    END IF;

    SET v_month = MONTH(p_boundary);
    IF v_month >= 7 THEN
        SET v_fy = YEAR(p_boundary) + 1;
        SET v_fq = FLOOR((v_month - 7) / 3) + 1;
    ELSE
        SET v_fy = YEAR(p_boundary);
        SET v_fq = FLOOR((v_month + 5) / 3) + 1;
    END IF;
    SET v_part_name = CONCAT('p_FY', SUBSTRING(v_fy, 3, 2), 'Q', v_fq);

    IF EXISTS (
        SELECT 1 FROM information_schema.PARTITIONS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = p_table_name
          AND PARTITION_NAME = v_part_name
    ) THEN
        SELECT 'already_exists' AS result, v_part_name AS partition_name;
    ELSE
        SELECT IFNULL(MAX(CAST(PARTITION_DESCRIPTION AS UNSIGNED)), 30) + 5
        INTO v_next_limit
        FROM information_schema.PARTITIONS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'AZURE_VM'
          AND PARTITION_DESCRIPTION != 'MAXVALUE';

        SET @sql_reorg = CONCAT(
            'ALTER TABLE `', p_table_name, '` REORGANIZE PARTITION p_catch INTO (',
            'PARTITION `', v_part_name, '` VALUES LESS THAN (', v_next_limit, '), ',
            'PARTITION p_catch VALUES LESS THAN MAXVALUE)'
        );
        PREPARE stmt_reorg FROM @sql_reorg;
        EXECUTE stmt_reorg;
        DEALLOCATE PREPARE stmt_reorg;

        SELECT 'partition_added' AS result, v_part_name AS partition_name;
    END IF;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- 6. sp_forecast_database
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_forecast_database;
DELIMITER //
CREATE PROCEDURE sp_forecast_database(
    IN p_event_id INT UNSIGNED
)
SQL SECURITY DEFINER
proc_db: BEGIN
    DECLARE v_db_allocated_mb DECIMAL(12,2) DEFAULT 10240.0;
    DECLARE v_at_risk_factor DECIMAL(5,2) DEFAULT 1.25;
    DECLARE v_n INT DEFAULT 0;
    DECLARE v_slope DECIMAL(14,6) DEFAULT 0.0;
    DECLARE v_intercept DECIMAL(14,4) DEFAULT 0.0;
    DECLARE v_current_mb DECIMAL(12,2) DEFAULT 0.0;
    DECLARE v_days_to_90 INT DEFAULT NULL;

    DECLARE v_event_duration_days INT DEFAULT 30;
    DECLARE v_start_date DATE;
    DECLARE v_end_date DATE;
    DECLARE v_steam_app_id INT;
    DECLARE v_expected_lift DECIMAL(10,4);
    DECLARE v_event_lift DECIMAL(10,4) DEFAULT 0.0;

    DECLARE v_current_rows BIGINT DEFAULT 2695548;
    DECLARE v_rows_per_day BIGINT DEFAULT 90000;
    DECLARE v_projected_rows BIGINT;
    DECLARE v_projected_growth_mb DECIMAL(14,4);

    DECLARE v_forecast_id INT;
    DECLARE v_as_of_date DATE;

    -- Read CONFIG
    SELECT CAST(config_value AS DECIMAL(12,2)) INTO v_db_allocated_mb FROM CONFIG WHERE config_key = 'DB_ALLOCATED_MB';
    SELECT CAST(config_value AS DECIMAL(5,2)) INTO v_at_risk_factor FROM CONFIG WHERE config_key = 'AT_RISK_FACTOR';
    IF v_db_allocated_mb IS NULL THEN SET v_db_allocated_mb = IFNULL(@db_allocated_mb, 10240.0); END IF;
    IF v_at_risk_factor IS NULL THEN SET v_at_risk_factor = 1.25; END IF;

    -- Event details for lift calculation
    SELECT start_date, end_date, steam_app_id, expected_lift
    INTO v_start_date, v_end_date, v_steam_app_id, v_expected_lift
    FROM BUSINESS_EVENT
    WHERE event_id = p_event_id;

    IF v_start_date IS NOT NULL AND v_end_date IS NOT NULL AND v_end_date > v_start_date THEN
        SET v_event_duration_days = DATEDIFF(v_end_date, v_start_date) + 1;
    ELSE
        SET v_event_duration_days = 30;
    END IF;

    -- Event lift lookup from EVENT_WINDOW_MONTH
    IF v_steam_app_id IS NOT NULL THEN
        SELECT IFNULL(AVG(lift_ratio) - 1.0, 0.0)
        INTO v_event_lift
        FROM EVENT_WINDOW_MONTH
        WHERE event_id = p_event_id
          AND month_offset BETWEEN 0 AND 2
          AND lift_ratio IS NOT NULL;
    ELSEIF v_expected_lift IS NOT NULL THEN
        SET v_event_lift = v_expected_lift - 1.0;
    ELSE
        SET v_event_lift = 0.0;
    END IF;

    -- Linear regression over DB_SIZE_SNAPSHOT (last 90 days)
    SELECT
        COUNT(*),
        IFNULL(MAX(y), 0.0),
        CASE
            WHEN COUNT(*) >= 2 AND (COUNT(*) * SUM(x * x) - SUM(x) * SUM(x)) != 0 THEN
                (COUNT(*) * SUM(x * y) - SUM(x) * SUM(y)) / (COUNT(*) * SUM(x * x) - SUM(x) * SUM(x))
            ELSE 0.0
        END
    INTO v_n, v_current_mb, v_slope
    FROM (
        SELECT
            DATEDIFF(snapped_at, CURDATE()) AS x,
            total_data_mb                   AS y
        FROM DB_SIZE_SNAPSHOT
        WHERE snapped_at >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
    ) snap;

    SET v_as_of_date = IFNULL(@as_of_date, CURDATE());

    -- Create FORECAST header
    INSERT INTO FORECAST (kind, event_id, as_of_date, created_at)
    VALUES ('database', p_event_id, v_as_of_date, NOW(3));
    SET v_forecast_id = LAST_INSERT_ID();

    -- Guard: slope <= 0
    IF v_slope <= 0 THEN
        SET v_days_to_90 = NULL;
        INSERT INTO FORECAST_LINE (forecast_id, line_no, metric_name, metric_value, error_indicator)
        VALUES
            (v_forecast_id, 1, 'slope_mb_per_day', v_slope, 'slope_non_positive'),
            (v_forecast_id, 2, 'days_to_90_pct', NULL, 'slope_non_positive');

        SELECT forecast_id, line_no, metric_name, metric_value, error_indicator
        FROM FORECAST_LINE
        WHERE forecast_id = v_forecast_id
        ORDER BY line_no;

        LEAVE proc_db;
    END IF;

    -- Days to 90% allocation
    IF (0.90 * v_db_allocated_mb) > v_current_mb THEN
        SET v_days_to_90 = ROUND((0.90 * v_db_allocated_mb - v_current_mb) / v_slope);
    ELSE
        SET v_days_to_90 = 0;
    END IF;

    -- Projected growth
    SET v_projected_growth_mb = ROUND(v_slope * (1.0 + v_event_lift) * v_event_duration_days, 4);
    SET v_projected_rows = ROUND(v_current_rows + (v_rows_per_day * v_event_duration_days * (1.0 + v_event_lift)));

    INSERT INTO FORECAST_LINE (forecast_id, line_no, metric_name, metric_value, error_indicator)
    VALUES
        (v_forecast_id, 1, 'slope_mb_per_day', v_slope, NULL),
        (v_forecast_id, 2, 'current_mb', v_current_mb, NULL),
        (v_forecast_id, 3, 'days_to_90_pct', v_days_to_90, NULL),
        (v_forecast_id, 4, 'projected_growth_mb', v_projected_growth_mb, NULL),
        (v_forecast_id, 5, 'projected_rows', v_projected_rows, NULL);

    IF v_days_to_90 IS NOT NULL AND v_days_to_90 < 30 THEN
        INSERT INTO FORECAST_LINE (forecast_id, line_no, metric_name, metric_value, error_indicator)
        VALUES (v_forecast_id, 6, 'warning_30_day', v_days_to_90, 'capacity threshold reached within 30 days');
    END IF;

    SELECT forecast_id, line_no, metric_name, metric_value, error_indicator
    FROM FORECAST_LINE
    WHERE forecast_id = v_forecast_id
    ORDER BY line_no;

END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- 7. sp_margin_decomposition (Updated)
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
-- 8. sp_update_pricing_rule (Updated)
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
    DECLARE v_curr_ver INT;

    UPDATE PRICING_RULE
       SET price_value = p_new_value,
           version     = version + 1
     WHERE rule_id = p_rule_id
       AND version = p_expected_version;

    IF ROW_COUNT() = 1 THEN
        SET p_status = 'OK';
    ELSE
        SELECT version INTO v_curr_ver FROM PRICING_RULE WHERE rule_id = p_rule_id;
        INSERT INTO CONFLICT_LOG (
            rule_id,
            attempted_by,
            stale_version,
            current_version,
            at
        ) VALUES (
            p_rule_id,
            SUBSTRING_INDEX(USER(), '@', 1),
            p_expected_version,
            v_curr_ver,
            NOW(3)
        );
        SET p_status = 'CONFLICT';
    END IF;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- 9. Update v_partitions to include AZURE_VM
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_partitions;
CREATE SQL SECURITY DEFINER VIEW v_partitions AS
SELECT *
FROM information_schema.PARTITIONS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME IN ('AZURE_VM', 'FACT_SALES');

-- ---------------------------------------------------------------------------
-- 10. Re-apply EXECUTE grants on all routines to proper roles
-- ---------------------------------------------------------------------------
GRANT EXECUTE ON PROCEDURE margin_db.sp_margin_decomposition TO 'business_analyst';
GRANT EXECUTE ON PROCEDURE margin_db.sp_forecast_business     TO 'business_analyst';
GRANT EXECUTE ON PROCEDURE margin_db.sp_update_pricing_rule   TO 'business_analyst';

GRANT EXECUTE ON PROCEDURE margin_db.sp_apply_index        TO 'db_engineer';
GRANT EXECUTE ON PROCEDURE margin_db.sp_reject_index       TO 'db_engineer';
GRANT EXECUTE ON PROCEDURE margin_db.sp_add_partition      TO 'db_engineer';
GRANT EXECUTE ON PROCEDURE margin_db.sp_snapshot_sizes     TO 'db_engineer';
GRANT EXECUTE ON PROCEDURE margin_db.sp_forecast_database  TO 'db_engineer';

GRANT EXECUTE ON PROCEDURE margin_db.sp_recommend_index    TO 'role_telemetry';
GRANT EXECUTE ON PROCEDURE margin_db.sp_insert_qe          TO 'role_telemetry';
GRANT EXECUTE ON PROCEDURE margin_db.sp_insert_qpn         TO 'role_telemetry';
GRANT EXECUTE ON PROCEDURE margin_db.sp_record_index_test  TO 'role_telemetry';
GRANT EXECUTE ON PROCEDURE margin_db.sp_flag_query         TO 'role_telemetry';
