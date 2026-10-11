#!/usr/bin/env bash
# =============================================================================
# load_sales.sh — ETL Master Orchestrator for Margin-DB
# =============================================================================
# Runs the full real-data ETL pipeline end-to-end.
# Requirements: 4.8, 4.10, 4.11
#
# IMPORTANT: NO synthetic data generation. All steps use real, operator-supplied
# data files only. Never run gen_synthetic_sales.py from this script.
#
# Pipeline sequence:
#   1. fetch_edgar.py
#   2. load_financials.py
#   3. [HUMAN] confirm data/raw/segments.csv is present
#   3b. load_segments_gaming.py
#   4. load_steam.py
#   5. load_trends.py
#   6. [HUMAN] confirm data/raw/events.csv is present
#   7. load_events.py
#   8. compute_event_windows.py
#   9. load_azure_vm.py  (real Azure Public Dataset V2 VM trace; 2,695,548 rows)
#  10. Final row-count reconciliation check
#
# Usage:
#   cd /path/to/margin-db
#   bash etl/load_sales.sh
#
# Environment:
#   All variables read from .env at project root.
#   MYSQL_PWD is set from ADMIN_PASSWORD — no -p flag used.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# 0. Resolve project root (directory containing this script's parent)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ---------------------------------------------------------------------------
# 1. Helper: UTC-stamped log
# ---------------------------------------------------------------------------
log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
}

# ---------------------------------------------------------------------------
# 2. Trap: log filename + step on any error, then exit non-zero
# ---------------------------------------------------------------------------
CURRENT_STEP="(unknown)"
on_error() {
    local exit_code=$?
    log "ERROR in step: ${CURRENT_STEP} (exit code ${exit_code}) — aborting pipeline."
    log "Source: ${BASH_SOURCE[0]}"
    exit "${exit_code}"
}
trap on_error ERR

# ---------------------------------------------------------------------------
# 3. Load .env from project root
# ---------------------------------------------------------------------------
CURRENT_STEP="source .env"
log "Loading environment from ${PROJECT_ROOT}/.env ..."
# shellcheck source=/dev/null
source "${PROJECT_ROOT}/.env"

# Required variables — fail fast if any are unset
: "${DB_HOST:?DB_HOST not set in .env}"
: "${DB_PORT:?DB_PORT not set in .env}"
: "${DB_NAME:?DB_NAME not set in .env}"
: "${ADMIN_USER:?ADMIN_USER not set in .env}"
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD not set in .env}"

# Export MYSQL_PWD so mysql CLI never needs -p flag
export MYSQL_PWD="${ADMIN_PASSWORD}"

# ---------------------------------------------------------------------------
# 4. Paths
# ---------------------------------------------------------------------------
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python"
ETL_DIR="${PROJECT_ROOT}/etl"
SQL_DIR="${PROJECT_ROOT}/sql"
DATA_CLEAN="${PROJECT_ROOT}/data/clean"
DATA_RAW="${PROJECT_ROOT}/data/raw"

# (No FACT_SALES trigger in real-data pipeline — FACT_SALES is unused)

# ---------------------------------------------------------------------------
# 5. MySQL CLI helper (no -p flag; MYSQL_PWD provides the password)
# ---------------------------------------------------------------------------
mysql_cli() {
    mysql \
        -h "${DB_HOST}" \
        -P "${DB_PORT}" \
        -u "${ADMIN_USER}" \
        "${DB_NAME}" \
        "$@"
}

# ---------------------------------------------------------------------------
# 6. Verify python venv exists
# ---------------------------------------------------------------------------
CURRENT_STEP="verify venv"
if [[ ! -x "${VENV_PYTHON}" ]]; then
    log "ERROR: Python venv not found at ${VENV_PYTHON}. Run: python3 -m venv .venv && .venv/bin/pip install -r etl/requirements.txt"
    exit 1
fi

# ---------------------------------------------------------------------------
# 7. Start 30-minute background timeout watchdog
#    If the main process exceeds 1800 s, the watchdog kills it.
# ---------------------------------------------------------------------------
PARENT_PID=$$
(
    sleep 1800
    log "TIMEOUT: 30-minute limit reached. Killing pipeline (PID ${PARENT_PID})."
    kill -TERM "${PARENT_PID}" 2>/dev/null || true
) &
WATCHDOG_PID=$!
# Clean up watchdog on normal exit
trap 'kill "${WATCHDOG_PID}" 2>/dev/null || true' EXIT

log "Pipeline started. Watchdog PID=${WATCHDOG_PID}. Hard limit: 30 minutes."

# =============================================================================
# PIPELINE STEPS
# =============================================================================

# ---------------------------------------------------------------------------
# Step 1: fetch_edgar.py
# ---------------------------------------------------------------------------
CURRENT_STEP="fetch_edgar.py"
log "Starting ${CURRENT_STEP} ..."
"${VENV_PYTHON}" "${ETL_DIR}/fetch_edgar.py"
log "Completed ${CURRENT_STEP}."

# ---------------------------------------------------------------------------
# Step 2: load_financials.py
# ---------------------------------------------------------------------------
CURRENT_STEP="load_financials.py"
log "Starting ${CURRENT_STEP} ..."
"${VENV_PYTHON}" "${ETL_DIR}/load_financials.py"
log "Completed ${CURRENT_STEP}."

# ---------------------------------------------------------------------------
# Step 3: HUMAN STEP — confirm segments.csv is present (Gaming revenue series)
# ---------------------------------------------------------------------------
CURRENT_STEP="HUMAN confirm segments.csv (Gaming revenue)"
log "HUMAN STEP: Confirm data/raw/segments.csv is present and ready."
echo ""
echo "  Expected file : ${DATA_RAW}/segments.csv"
echo "  Required cols : fiscal_year, fiscal_quarter, segment,"
echo "                  'revenue (in million dollars)', operating_income,"
echo "                  source_url, Months"
echo "  Note          : segment must be 'Gaming' for all rows."
echo "                  This file loads into GAMING_REVENUE_SERIES (not QUARTERLY_FINANCIALS)."
echo ""
read -r -p "  Has data/raw/segments.csv been placed and verified? [YES/no]: " CONFIRM_SEGMENTS
if [[ "${CONFIRM_SEGMENTS}" != "YES" ]]; then
    log "Operator did not confirm segments.csv. Aborting pipeline."
    exit 1
fi
log "Operator confirmed segments.csv."

# ---------------------------------------------------------------------------
# Step 3b: load_segments_gaming.py
# ---------------------------------------------------------------------------
CURRENT_STEP="load_segments_gaming.py"
log "Starting ${CURRENT_STEP} ..."
"${VENV_PYTHON}" "${ETL_DIR}/load_segments_gaming.py"
log "Completed ${CURRENT_STEP}."

# ---------------------------------------------------------------------------
# Step 4: load_steam.py
# ---------------------------------------------------------------------------
CURRENT_STEP="load_steam.py"
log "Starting ${CURRENT_STEP} ..."
"${VENV_PYTHON}" "${ETL_DIR}/load_steam.py"
log "Completed ${CURRENT_STEP}."

# ---------------------------------------------------------------------------
# Step 5: load_trends.py
# ---------------------------------------------------------------------------
CURRENT_STEP="load_trends.py"
log "Starting ${CURRENT_STEP} ..."
"${VENV_PYTHON}" "${ETL_DIR}/load_trends.py"
log "Completed ${CURRENT_STEP}."

# ---------------------------------------------------------------------------
# Step 6: HUMAN STEP — confirm events.csv is present
# ---------------------------------------------------------------------------
CURRENT_STEP="HUMAN confirm events.csv"
log "HUMAN STEP: Confirm data/raw/events.csv is present and ready."
echo ""
echo "  Expected file : ${DATA_RAW}/events.csv"
echo "  Required cols : event_type, name, start_date, end_date, expected_lift,"
echo "                  steam_app_id (INT or blank), source_url"
echo "  Must include  : at least one game_launch event with start_date > today"
echo ""
read -r -p "  Has events.csv been placed at data/raw/events.csv? [YES/no]: " CONFIRM_EVENTS
if [[ "${CONFIRM_EVENTS}" != "YES" ]]; then
    log "Operator did not confirm events.csv. Aborting pipeline."
    exit 1
fi
log "Operator confirmed events.csv."

# ---------------------------------------------------------------------------
# Step 7: load_events.py
# ---------------------------------------------------------------------------
CURRENT_STEP="load_events.py"
log "Starting ${CURRENT_STEP} ..."
"${VENV_PYTHON}" "${ETL_DIR}/load_events.py"
log "Completed ${CURRENT_STEP}."

# ---------------------------------------------------------------------------
# Step 8: compute_event_windows.py
# ---------------------------------------------------------------------------
CURRENT_STEP="compute_event_windows.py"
log "Starting ${CURRENT_STEP} ..."
"${VENV_PYTHON}" "${ETL_DIR}/compute_event_windows.py"
log "Completed ${CURRENT_STEP}."

# ---------------------------------------------------------------------------
# Step 9: load_azure_vm.py — real Azure Public Dataset V2 VM trace
# ---------------------------------------------------------------------------
CURRENT_STEP="load_azure_vm.py"
log "Starting ${CURRENT_STEP} — loading Azure VM workload data (2,695,548 rows) ..."
"${VENV_PYTHON}" "${ETL_DIR}/load_azure_vm.py"
log "Completed ${CURRENT_STEP}."

# ---------------------------------------------------------------------------
# Step 10: Final row-count reconciliation
# ---------------------------------------------------------------------------
CURRENT_STEP="row-count reconciliation"
log "Starting ${CURRENT_STEP} ..."

AZURE_COUNT=$(
    "${VENV_PYTHON}" -c "
import mysql.connector, os
from pathlib import Path
from dotenv import dotenv_values
env = dotenv_values(Path('${PROJECT_ROOT}/.env'))
cnx = mysql.connector.connect(
    host=env['DB_HOST'], port=int(env['DB_PORT']),
    user=env['ADMIN_USER'], password=env['ADMIN_PASSWORD'],
    database=env['DB_NAME']
)
cur = cnx.cursor()
cur.execute('SELECT COUNT(*) FROM AZURE_VM')
print(cur.fetchone()[0])
cur.close(); cnx.close()
" 2>/dev/null
)

if [[ "${AZURE_COUNT}" -eq 2695548 ]]; then
    log "Reconciliation PASSED — AZURE_VM row count: ${AZURE_COUNT} (expected 2,695,548)."
else
    log "WARNING: AZURE_VM row count is ${AZURE_COUNT}; expected 2,695,548. Investigate before proceeding."
fi

QF_COUNT=$(
    "${VENV_PYTHON}" -c "
import mysql.connector
from pathlib import Path
from dotenv import dotenv_values
env = dotenv_values(Path('${PROJECT_ROOT}/.env'))
cnx = mysql.connector.connect(
    host=env['DB_HOST'], port=int(env['DB_PORT']),
    user=env['ADMIN_USER'], password=env['ADMIN_PASSWORD'],
    database=env['DB_NAME']
)
cur = cnx.cursor()
cur.execute('SELECT COUNT(*) FROM QUARTERLY_FINANCIALS')
print(cur.fetchone()[0])
cur.close(); cnx.close()
" 2>/dev/null
)
log "QUARTERLY_FINANCIALS row count: ${QF_COUNT}"

log "Reconciliation check complete."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
log "ETL pipeline completed successfully."


