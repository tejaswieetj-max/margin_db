-- =============================================================================
-- 06_seed_reference.sql — Margin-DB: All Reference / Dimension Data
-- =============================================================================
-- Run order: after 05_triggers_events.sql, before 07_grants.sql.
-- All inserts are idempotent: INSERT IGNORE or INSERT ... ON DUPLICATE KEY UPDATE.
-- No GRANT statements appear in this file (all GRANTs are in 07_grants.sql).
--
-- Applies to: MySQL 8.0 | Database: margin_db
-- Requirements: 3.7, 2.7
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. COMPANY_SEGMENT (3 rows)
--    Uses INSERT IGNORE so re-runs are no-ops.
-- ---------------------------------------------------------------------------
-- Three official Microsoft reporting segments (PBP, IC, MPC).
-- Gaming is a product line inside MPC, NOT a segment.
-- NOTE: for fresh installs this seeds the correct three segments.
-- For upgrades from a schema that had a 'Gaming' segment,
-- run sql/migrations/09_fix_segments.sql first.
INSERT IGNORE INTO COMPANY_SEGMENT (name) VALUES
    ('Productivity and Business Processes'),
    ('Intelligent Cloud'),
    ('More Personal Computing');

-- ---------------------------------------------------------------------------
-- 2. DIM_DATE — 2022-07-01 through 2027-06-30
--    Generated via a temporary stored procedure that loops day-by-day.
--    Microsoft fiscal year: FY starts July 1, ends June 30.
--      Jul–Jun of calendar year Y → YYYY+1 fiscal year (e.g., Jul 2022 = FY2023)
--    Fiscal quarters: Q1=Jul-Sep, Q2=Oct-Dec, Q3=Jan-Mar, Q4=Apr-Jun
--    is_black_friday: 4th Friday of November each year
--    is_holiday: Christmas (Dec 25), New Year's Day (Jan 1), Thanksgiving
--                (4th Thursday of November), Black Friday (4th Friday of November),
--                New Year's Eve (Dec 31)
-- ---------------------------------------------------------------------------

DROP PROCEDURE IF EXISTS _seed_dim_date;

DELIMITER $$

CREATE PROCEDURE _seed_dim_date()
BEGIN
    DECLARE v_date        DATE    DEFAULT '2022-07-01';
    DECLARE v_end_date    DATE    DEFAULT '2027-06-30';
    DECLARE v_date_id     INT;
    DECLARE v_fy          SMALLINT;
    DECLARE v_fq          TINYINT;
    DECLARE v_month       TINYINT;
    DECLARE v_day         TINYINT;
    DECLARE v_dow         TINYINT;   -- 1=Sunday .. 7=Saturday (DAYOFWEEK)
    DECLARE v_is_bf       BOOLEAN;
    DECLARE v_is_holiday  BOOLEAN;
    -- Variables for computing the 4th Thursday / 4th Friday of November
    DECLARE v_year        INT;
    DECLARE v_nov1        DATE;
    DECLARE v_nov1_dow    TINYINT;
    DECLARE v_thanksgiving DATE;
    DECLARE v_black_friday DATE;

    WHILE v_date <= v_end_date DO
        SET v_date_id   = TO_DAYS(v_date);
        SET v_month     = MONTH(v_date);
        SET v_day       = DAYOFMONTH(v_date);
        SET v_year      = YEAR(v_date);

        -- Microsoft fiscal year: months Jul(7)–Dec(12) map to current_year+1
        --                        months Jan(1)–Jun(6)  map to current_year
        IF v_month >= 7 THEN
            SET v_fy = v_year + 1;
        ELSE
            SET v_fy = v_year;
        END IF;

        -- Fiscal quarter
        SET v_fq = CASE v_month
            WHEN 7  THEN 1  -- Q1 Jul
            WHEN 8  THEN 1  -- Q1 Aug
            WHEN 9  THEN 1  -- Q1 Sep
            WHEN 10 THEN 2  -- Q2 Oct
            WHEN 11 THEN 2  -- Q2 Nov
            WHEN 12 THEN 2  -- Q2 Dec
            WHEN 1  THEN 3  -- Q3 Jan
            WHEN 2  THEN 3  -- Q3 Feb
            WHEN 3  THEN 3  -- Q3 Mar
            WHEN 4  THEN 4  -- Q4 Apr
            WHEN 5  THEN 4  -- Q4 May
            WHEN 6  THEN 4  -- Q4 Jun
            ELSE 0
        END;

        -- Compute Thanksgiving (4th Thursday of November) and Black Friday
        -- for the calendar year of this date.
        -- DAYOFWEEK: 1=Sunday, 2=Monday, ..., 5=Thursday, 6=Friday, 7=Saturday
        SET v_nov1     = DATE(CONCAT(v_year, '-11-01'));
        SET v_nov1_dow = DAYOFWEEK(v_nov1);

        -- Days until the first Thursday from Nov 1
        -- Thursday = DAYOFWEEK 5
        -- offset = (5 - v_nov1_dow + 7) MOD 7
        SET v_thanksgiving = DATE_ADD(v_nov1,
            INTERVAL ((5 - v_nov1_dow + 7) MOD 7 + 21) DAY);
        SET v_black_friday = DATE_ADD(v_thanksgiving, INTERVAL 1 DAY);

        -- is_black_friday
        SET v_is_bf = (v_date = v_black_friday);

        -- is_holiday: Jan 1, Dec 25, Dec 31, Thanksgiving, Black Friday
        SET v_is_holiday = (
               (v_month = 1  AND v_day = 1)    -- New Year's Day
            OR (v_month = 12 AND v_day = 25)   -- Christmas
            OR (v_month = 12 AND v_day = 31)   -- New Year's Eve
            OR (v_date = v_thanksgiving)        -- Thanksgiving (4th Thu Nov)
            OR (v_date = v_black_friday)        -- Black Friday (4th Fri Nov)
        );

        INSERT INTO DIM_DATE
            (date_id, calendar_date, fiscal_year, fiscal_quarter,
             is_black_friday, is_holiday)
        VALUES
            (v_date_id, v_date, v_fy, v_fq, v_is_bf, v_is_holiday)
        ON DUPLICATE KEY UPDATE
            fiscal_year     = VALUES(fiscal_year),
            fiscal_quarter  = VALUES(fiscal_quarter),
            is_black_friday = VALUES(is_black_friday),
            is_holiday      = VALUES(is_holiday);

        SET v_date = DATE_ADD(v_date, INTERVAL 1 DAY);
    END WHILE;
END$$

DELIMITER ;

CALL _seed_dim_date();

DROP PROCEDURE IF EXISTS _seed_dim_date;

-- ---------------------------------------------------------------------------
-- 3. DIM_DEVICE (10 rows across 5 platforms)
-- ---------------------------------------------------------------------------
INSERT IGNORE INTO DIM_DEVICE (platform, device_family, oem) VALUES
    -- Xbox Console
    ('Xbox',        'Xbox Console',  NULL),
    -- Windows PC — Microsoft Surface
    ('Windows PC',  'Surface',       'Microsoft'),
    -- Windows PC — Generic OEM variants
    ('Windows PC',  'Windows PC',    'Dell'),
    ('Windows PC',  'Windows PC',    'HP'),
    ('Windows PC',  'Windows PC',    'Lenovo'),
    -- Mobile
    ('Mobile',      'Android',       NULL),
    ('Mobile',      'iOS',           NULL),
    -- Cloud Gaming
    ('Cloud',       'Cloud Gaming',  NULL),
    -- Mac
    ('Mac',         'MacBook',       NULL);

-- ---------------------------------------------------------------------------
-- 4. DIM_REGION (3 rows)
-- ---------------------------------------------------------------------------
INSERT IGNORE INTO DIM_REGION (name, parent_region) VALUES
    ('Americas', NULL),
    ('EMEA',     NULL),
    ('APAC',     NULL);

-- ---------------------------------------------------------------------------
-- 5. DIM_CHANNEL (3 rows)
-- ---------------------------------------------------------------------------
INSERT IGNORE INTO DIM_CHANNEL (name) VALUES
    ('Digital'),
    ('Physical'),
    ('Subscription');

-- ---------------------------------------------------------------------------
-- 6. PRODUCT — 101 products total
--    40 Gaming products: segment=MPC, product_line='Gaming', unit_cost_ratio 0.35
--       (Gaming sits inside MPC; product_line tag distinguishes them)
--    31 PBP products: segment=PBP, unit_cost_ratio 0.25
--    30 MPC (non-Gaming) products: segment=MPC, product_line NULL, unit_cost_ratio 0.40
--
--    unit_cost_ratio is SYNTHETIC ONLY — used by gen_synthetic_sales.py to
--    compute FACT_SALES.cost_amount. It is never reported as real margin data.
--    Gaming products: cost_amount is NULL in FACT_SALES (no Gaming cost disclosed).
--
--    Idempotent via INSERT IGNORE (SKU column has no unique constraint in DDL
--    but product_id is AUTO_INCREMENT; on re-run, duplicate product_id rows
--    simply fail silently because INSERT IGNORE skips them; the ON DUPLICATE
--    KEY UPDATE path is not viable here without a unique key on (sku), so we
--    rely on INSERT IGNORE against the AUTO_INCREMENT PK — effectively a no-op
--    if the row already exists).  For true upsert semantics the operator may
--    add UNIQUE KEY uq_sku (sku) after initial migration.
--
--    To ensure idempotency across re-runs we use explicit product_id values
--    so duplicate inserts are caught by the PK.
-- ---------------------------------------------------------------------------

-- ---- Gaming products (segment=MPC, product_line='Gaming') -----
-- 40 products; unit_cost_ratio synthetic only; cost_amount NULL in FACT_SALES

INSERT INTO PRODUCT (product_id, segment_id, name, sku, unit_cost_ratio, product_line) VALUES
  (1,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Xbox Series X Console',                 'GX001', 0.3500, 'Gaming'),
  (2,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Xbox Series S Console',                 'GX002', 0.3500, 'Gaming'),
  (3,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Xbox One X Console',                    'GX003', 0.3500, 'Gaming'),
  (4,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Xbox One S Console',                    'GX004', 0.3500, 'Gaming'),
  (5,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Xbox Elite Wireless Controller Series 2','GX005', 0.3500, 'Gaming'),
  (6,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Xbox Wireless Controller',              'GX006', 0.3500, 'Gaming'),
  (7,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Game Pass Ultimate (1-Month)',          'GX007', 0.3500, 'Gaming'),
  (8,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Game Pass Ultimate (3-Month)',          'GX008', 0.3500, 'Gaming'),
  (9,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Game Pass Ultimate (12-Month)',         'GX009', 0.3500, 'Gaming'),
  (10, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Xbox Game Pass PC (1-Month)',           'GX010', 0.3500, 'Gaming'),
  (11, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Xbox Game Pass PC (3-Month)',           'GX011', 0.3500, 'Gaming'),
  (12, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Halo Infinite',                        'GX012', 0.3500, 'Gaming'),
  (13, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Halo Infinite — Campaign DLC',         'GX013', 0.3500, 'Gaming'),
  (14, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Forza Horizon 5',                      'GX014', 0.3500, 'Gaming'),
  (15, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Forza Horizon 5 — Premium Add-Ons Bundle','GX015', 0.3500, 'Gaming'),
  (16, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Forza Motorsport (2023)',               'GX016', 0.3500, 'Gaming'),
  (17, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Call of Duty: Modern Warfare III',     'GX017', 0.3500, 'Gaming'),
  (18, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Call of Duty: Warzone (Season Pass)',  'GX018', 0.3500, 'Gaming'),
  (19, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Call of Duty: Modern Warfare II',      'GX019', 0.3500, 'Gaming'),
  (20, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Sea of Thieves',                       'GX020', 0.3500, 'Gaming'),
  (21, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Sea of Thieves — Plunder Pass',        'GX021', 0.3500, 'Gaming'),
  (22, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Minecraft Java & Bedrock Edition',     'GX022', 0.3500, 'Gaming'),
  (23, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Minecraft Dungeons',                   'GX023', 0.3500, 'Gaming'),
  (24, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Minecraft Legends',                    'GX024', 0.3500, 'Gaming'),
  (25, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Starfield',                            'GX025', 0.3500, 'Gaming'),
  (26, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Starfield — Premium Edition Upgrade',  'GX026', 0.3500, 'Gaming'),
  (27, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Redfall',                              'GX027', 0.3500, 'Gaming'),
  (28, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Hi-Fi Rush',                           'GX028', 0.3500, 'Gaming'),
  (29, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Psychonauts 2',                        'GX029', 0.3500, 'Gaming'),
  (30, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Age of Empires IV',                    'GX030', 0.3500, 'Gaming'),
  (31, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Microsoft Flight Simulator 2024',      'GX031', 0.3500, 'Gaming'),
  (32, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Microsoft Flight Simulator (2020)',    'GX032', 0.3500, 'Gaming'),
  (33, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Gears 5',                              'GX033', 0.3500, 'Gaming'),
  (34, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Gears Tactics',                        'GX034', 0.3500, 'Gaming'),
  (35, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Ori and the Will of the Wisps',        'GX035', 0.3500, 'Gaming'),
  (36, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Pentiment',                            'GX036', 0.3500, 'Gaming'),
  (37, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Grounded',                             'GX037', 0.3500, 'Gaming'),
  (38, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'The Elder Scrolls Online (Base Game)', 'GX038', 0.3500, 'Gaming'),
  (39, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Deathloop',                            'GX039', 0.3500, 'Gaming'),
  (40, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Dishonored 2',                         'GX040', 0.3500, 'Gaming')
ON DUPLICATE KEY UPDATE
    name            = VALUES(name),
    sku             = VALUES(sku),
    unit_cost_ratio = VALUES(unit_cost_ratio),
    product_line    = VALUES(product_line);

-- ---- Productivity and Business Processes (PBP) (segment_id via subquery) -------------
-- ~31 products, unit_cost_ratio = 0.2500

INSERT INTO PRODUCT (product_id, segment_id, name, sku, unit_cost_ratio) VALUES
  (41, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft 365 Personal (Annual)',          'PB001', 0.2500),
  (42, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft 365 Family (Annual)',            'PB002', 0.2500),
  (43, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft 365 Business Basic',            'PB003', 0.2500),
  (44, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft 365 Business Standard',         'PB004', 0.2500),
  (45, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft 365 Business Premium',          'PB005', 0.2500),
  (46, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft 365 Apps for Business',         'PB006', 0.2500),
  (47, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft 365 E3',                        'PB007', 0.2500),
  (48, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft 365 E5',                        'PB008', 0.2500),
  (49, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Azure — Pay-As-You-Go Subscription',      'PB009', 0.2500),
  (50, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Azure — Reserved Instance 1-Year',        'PB010', 0.2500),
  (51, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Azure — Reserved Instance 3-Year',        'PB011', 0.2500),
  (52, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Azure AI Services Subscription',          'PB012', 0.2500),
  (53, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Azure DevOps — Basic Plan',               'PB013', 0.2500),
  (54, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Azure DevOps — Basic + Test Plans',       'PB014', 0.2500),
  (55, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft Teams Essentials',              'PB015', 0.2500),
  (56, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft Teams Phone with Calling Plan', 'PB016', 0.2500),
  (57, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft Teams Rooms Pro',               'PB017', 0.2500),
  (58, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft Viva Suite',                    'PB018', 0.2500),
  (59, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft Copilot for Microsoft 365',     'PB019', 0.2500),
  (60, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Power BI Pro',                            'PB020', 0.2500),
  (61, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Power BI Premium Per User',               'PB021', 0.2500),
  (62, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Power Apps Per User Plan',                'PB022', 0.2500),
  (63, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Power Automate Per User Plan',            'PB023', 0.2500),
  (64, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Dynamics 365 Business Central Essentials','PB024', 0.2500),
  (65, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Dynamics 365 Sales Professional',         'PB025', 0.2500),
  (66, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Dynamics 365 Customer Service Professional','PB026', 0.2500),
  (67, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'LinkedIn Recruiter Lite (Annual)',        'PB027', 0.2500),
  (68, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'LinkedIn Sales Navigator Core (Annual)',  'PB028', 0.2500),
  (69, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft Intune Plan 1',                 'PB029', 0.2500),
  (70, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft Defender for Business',         'PB030', 0.2500),
  (71, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='Productivity and Business Processes'), 'Microsoft Defender for Endpoint P2',      'PB031', 0.2500)
ON DUPLICATE KEY UPDATE
    name            = VALUES(name),
    sku             = VALUES(sku),
    unit_cost_ratio = VALUES(unit_cost_ratio);

-- ---- More Personal Computing non-Gaming (segment=MPC, product_line NULL)
-- 30 products, unit_cost_ratio = 0.4000 (synthetic only)

INSERT INTO PRODUCT (product_id, segment_id, name, sku, unit_cost_ratio) VALUES
  (72,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Pro 9 (Intel, 256 GB)',             'MC001', 0.4000),
  (73,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Pro 9 (Intel, 512 GB)',             'MC002', 0.4000),
  (74,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Pro 9 5G',                          'MC003', 0.4000),
  (75,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Laptop 5 (13.5-inch)',              'MC004', 0.4000),
  (76,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Laptop 5 (15-inch)',                'MC005', 0.4000),
  (77,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Laptop Go 3',                       'MC006', 0.4000),
  (78,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Laptop Studio 2',                   'MC007', 0.4000),
  (79,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Go 3 (64 GB)',                      'MC008', 0.4000),
  (80,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Go 3 (128 GB)',                     'MC009', 0.4000),
  (81,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Studio 2+',                         'MC010', 0.4000),
  (82,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Pro Keyboard',                      'MC011', 0.4000),
  (83,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Arc Mouse',                         'MC012', 0.4000),
  (84,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Slim Pen 2',                        'MC013', 0.4000),
  (85,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Dock 2',                            'MC014', 0.4000),
  (86,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Headphones 2+',                     'MC015', 0.4000),
  (87,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Windows 11 Home',                           'MC016', 0.4000),
  (88,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Windows 11 Pro',                            'MC017', 0.4000),
  (89,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Windows 11 Pro for Workstations',           'MC018', 0.4000),
  (90,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Windows 11 Education',                      'MC019', 0.4000),
  (91,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Windows 11 Enterprise (Per Device)',        'MC020', 0.4000),
  (92,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Windows 365 Business (2 vCPU, 4 GB RAM)',   'MC021', 0.4000),
  (93,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Windows 365 Enterprise (4 vCPU, 16 GB RAM)','MC022', 0.4000),
  (94,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Microsoft Surface Earbuds',                 'MC023', 0.4000),
  (95,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Microsoft Modern Webcam',                   'MC024', 0.4000),
  (96,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Microsoft Wireless Desktop 900',            'MC025', 0.4000),
  (97,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Microsoft Bluetooth Mouse',                 'MC026', 0.4000),
  (98,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Microsoft Ergonomic Keyboard',              'MC027', 0.4000),
  (99,  (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Microsoft IntelliMouse Optical',            'MC028', 0.4000),
  (100, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Pro X',                             'MC029', 0.4000),
  (101, (SELECT segment_id FROM COMPANY_SEGMENT WHERE name='More Personal Computing'), 'Surface Book 3 (13.5-inch)',                'MC030', 0.4000)
ON DUPLICATE KEY UPDATE
    name            = VALUES(name),
    sku             = VALUES(sku),
    unit_cost_ratio = VALUES(unit_cost_ratio);

-- ---------------------------------------------------------------------------
-- 7. USER_SEGMENT_MAP
--    analyst_demo → MPC segment, product_line='Gaming'
--      (views filter to product_line='Gaming' so analyst_demo sees only Gaming products)
--    analyst_all  → all 3 segments, no product_line restriction
-- ---------------------------------------------------------------------------
INSERT IGNORE INTO USER_SEGMENT_MAP (db_user, segment_id, product_line)
    SELECT 'analyst_demo', segment_id, 'Gaming'
    FROM   COMPANY_SEGMENT
    WHERE  name = 'More Personal Computing';

INSERT IGNORE INTO USER_SEGMENT_MAP (db_user, segment_id, product_line)
    SELECT 'analyst_all', segment_id, NULL
    FROM   COMPANY_SEGMENT;

-- ---------------------------------------------------------------------------
-- 8. CONFIG (2 rows)
--    DB_ALLOCATED_MB: placeholder; overwritten by ETL after bulk load.
--    AT_RISK_FACTOR:  default at-risk multiplier for sp_forecast_database.
-- ---------------------------------------------------------------------------
INSERT INTO CONFIG (config_key, config_value) VALUES
    ('DB_ALLOCATED_MB', '10240'),
    ('AT_RISK_FACTOR',  '1.25')
ON DUPLICATE KEY UPDATE
    config_value = VALUES(config_value);

-- =============================================================================
-- End of 06_seed_reference.sql
-- Summary:
--   COMPANY_SEGMENT : 3 rows (PBP, IC, MPC — no Gaming row)
--   DIM_DATE        : 1827 rows (2022-07-01 to 2027-06-30, inclusive)
--   DIM_DEVICE      : 9 rows
--   DIM_REGION      : 3 rows (Americas, EMEA, APAC)
--   DIM_CHANNEL     : 3 rows (Digital, Physical, Subscription)
--   PRODUCT         : 101 rows
--                     40 Gaming (segment=MPC, product_line='Gaming')
--                     31 PBP (segment=PBP, product_line=NULL)
--                     30 MPC non-Gaming (segment=MPC, product_line=NULL)
--   USER_SEGMENT_MAP: 4 rows (analyst_demo→MPC+Gaming, analyst_all→all×NULL)
--   CONFIG          : 2 rows (DB_ALLOCATED_MB, AT_RISK_FACTOR)
-- =============================================================================
