-- =============================================================================
-- 02_roles.sql — Role objects and user accounts
-- =============================================================================
-- Creates MySQL roles and demo user accounts with password placeholders.
-- Passwords are substituted at runtime by envsubst (called from run_migrations.sh).
--
-- ALL GRANT statements (role assignments, privilege grants, and SET DEFAULT ROLE)
-- are located exclusively in sql/07_grants.sql. Nothing is granted here.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Roles
-- -----------------------------------------------------------------------------

CREATE ROLE IF NOT EXISTS 'business_analyst';
CREATE ROLE IF NOT EXISTS 'db_engineer';
CREATE ROLE IF NOT EXISTS 'role_telemetry';

-- -----------------------------------------------------------------------------
-- User accounts
-- Passwords come from environment variables via envsubst; never hardcoded.
-- -----------------------------------------------------------------------------

CREATE USER IF NOT EXISTS 'analyst_demo'@'localhost'
    IDENTIFIED BY '${DEMO_ANALYST_PASSWORD}';

CREATE USER IF NOT EXISTS 'analyst_all'@'localhost'
    IDENTIFIED BY '${DEMO_ANALYST_ALL_PASSWORD}';

CREATE USER IF NOT EXISTS 'engineer_demo'@'localhost'
    IDENTIFIED BY '${DEMO_ENGINEER_PASSWORD}';

CREATE USER IF NOT EXISTS 'svc_telemetry'@'localhost'
    IDENTIFIED BY '${SVC_TELEMETRY_PASSWORD}';
