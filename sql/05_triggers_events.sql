-- =============================================================================
-- 05_triggers_events.sql — Triggers and Scheduled Events for Margin-DB
-- =============================================================================
-- IMPORTANT: MySQL syntax only. No Oracle PL/SQL constructs.
-- No := operator, no DBMS_* packages, no DECLARE...EXCEPTION blocks,
-- no RAISE_APPLICATION_ERROR, no bare EXCEPTION keyword.
--
-- All triggers use DROP TRIGGER IF EXISTS + CREATE TRIGGER (Requirement 3.8).
-- All events use DROP EVENT IF EXISTS + CREATE EVENT.
-- All trigger bodies use SUBSTRING_INDEX(USER(),'@',1) — never CURRENT_USER()
-- (Requirements 5.4, 5.9).
--
-- NOTE: SET GLOBAL event_scheduler = ON is NOT included here.
-- That is a HUMAN STEP executed in Stage 0 setup (task 0.0).
-- See docs/stage_0_report.md. Running it inside a migration file would require
-- SUPER or SYSTEM_VARIABLES_ADMIN and is intentionally left to the operator.
--
-- Applies to: MySQL 8.0 | Database: margin_db
-- Requirements: 3.1, 5.3, 5.6, 5.7, 12.4, 17.2
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Trigger 1: trg_fact_sales_before_insert
-- Purpose: Validate that product_id exists in PRODUCT and segment_id exists
--          in COMPANY_SEGMENT before any INSERT into FACT_SALES.
--          Raises SQLSTATE '45000' if either FK reference is invalid.
-- Requirements: 3.1, 5.6, 5.9
-- ---------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_fact_sales_before_insert;
DELIMITER //
CREATE TRIGGER trg_fact_sales_before_insert
BEFORE INSERT ON FACT_SALES
FOR EACH ROW
BEGIN
    -- Validate product_id exists in PRODUCT
    IF NOT EXISTS (
        SELECT 1 FROM PRODUCT WHERE product_id = NEW.product_id
    ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Insert rejected: product_id does not exist in PRODUCT';
    END IF;

    -- Validate segment_id exists in COMPANY_SEGMENT
    IF NOT EXISTS (
        SELECT 1 FROM COMPANY_SEGMENT WHERE segment_id = NEW.segment_id
    ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'Insert rejected: segment_id does not exist in COMPANY_SEGMENT';
    END IF;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- Trigger 2: trg_pricing_rule_after_update
-- Purpose: Write an AUDIT_LOG row after any UPDATE on PRICING_RULE, capturing
--          old and new values as JSON objects.
-- Requirements: 3.1, 5.7, 5.9, 12.4
-- ---------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_pricing_rule_after_update;
DELIMITER //
CREATE TRIGGER trg_pricing_rule_after_update
AFTER UPDATE ON PRICING_RULE
FOR EACH ROW
BEGIN
    INSERT INTO AUDIT_LOG (
        table_name,
        operation,
        old_value,
        new_value,
        db_user,
        at
    )
    VALUES (
        'PRICING_RULE',
        'UPDATE',
        JSON_OBJECT(
            'rule_id',     OLD.rule_id,
            'price_value', OLD.price_value,
            'version',     OLD.version
        ),
        JSON_OBJECT(
            'rule_id',     NEW.rule_id,
            'price_value', NEW.price_value,
            'version',     NEW.version
        ),
        SUBSTRING_INDEX(USER(), '@', 1),
        NOW(3)
    );
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- Trigger 3: trg_pricing_rule_after_delete
-- Purpose: Write an AUDIT_LOG row after any DELETE on PRICING_RULE, capturing
--          the deleted row's values as a JSON object; new_value is NULL.
-- Requirements: 3.1, 5.7, 5.9, 12.4
-- ---------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_pricing_rule_after_delete;
DELIMITER //
CREATE TRIGGER trg_pricing_rule_after_delete
AFTER DELETE ON PRICING_RULE
FOR EACH ROW
BEGIN
    INSERT INTO AUDIT_LOG (
        table_name,
        operation,
        old_value,
        new_value,
        db_user,
        at
    )
    VALUES (
        'PRICING_RULE',
        'DELETE',
        JSON_OBJECT(
            'rule_id',     OLD.rule_id,
            'price_value', OLD.price_value,
            'version',     OLD.version
        ),
        NULL,
        SUBSTRING_INDEX(USER(), '@', 1),
        NOW(3)
    );
END //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- Event 1: ev_snapshot_sizes
-- Purpose: Daily event that calls sp_snapshot_sizes to record a DB_SIZE_SNAPSHOT
--          row. Starts immediately; recurs every 1 day.
-- Requirements: 5.3, 9.1, 17.2
--
-- NOTE: Requires event_scheduler=ON (set by the operator in Stage 0).
-- ---------------------------------------------------------------------------
DROP EVENT IF EXISTS ev_snapshot_sizes;
DELIMITER //
CREATE EVENT ev_snapshot_sizes
ON SCHEDULE EVERY 1 DAY
STARTS CURRENT_TIMESTAMP
DO CALL sp_snapshot_sizes() //
DELIMITER ;

-- ---------------------------------------------------------------------------
-- Event 2: ev_recalc_forecasts
-- Purpose: Nightly event that loops through every row in BUSINESS_EVENT and
--          calls sp_forecast_database for each event. Starts immediately;
--          recurs every 1 day.
-- Requirements: 5.3, 9.5, 17.2
--
-- NOTE: Requires event_scheduler=ON (set by the operator in Stage 0).
-- ---------------------------------------------------------------------------
DROP EVENT IF EXISTS ev_recalc_forecasts;
DELIMITER //
CREATE EVENT ev_recalc_forecasts
ON SCHEDULE EVERY 1 DAY
STARTS CURRENT_TIMESTAMP
DO BEGIN
    -- nightly stub: iterate BUSINESS_EVENT rows and call sp_forecast_database
    DECLARE v_event_id INT;
    DECLARE done       INT DEFAULT FALSE;
    DECLARE cur CURSOR FOR SELECT event_id FROM BUSINESS_EVENT;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = TRUE;
    OPEN cur;
    event_loop: LOOP
        FETCH cur INTO v_event_id;
        IF done THEN LEAVE event_loop; END IF;
        CALL sp_forecast_database(v_event_id);
    END LOOP;
    CLOSE cur;
END //
DELIMITER ;
