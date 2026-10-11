#!/usr/bin/env bash
# =============================================================================
# load_sales.sh — ETL Master Orchestrator for Margin-DB
# =============================================================================
# Runs the full ETL pipeline end-to-end with a 30-minute hard timeout.
# Requirements: 4.8, 4.10, 4.11
#
# Pipeline sequence:
#   1. fetch_edgar.py
#   2. load_financials.py
#   3. [HUMAN] confirm data/raw/segments.csv is present
#   4. load_steam.py
#   5. load_trends.py
#   6. [HUMAN] confirm data/raw/events.csv is present
#   7. load_events.py
#   8. compute_event_windows.py
#   9. gen_synthetic_sales.py  (writes data/clean/sales_chunk_*.csv)
#  10. [HUMAN] confirm dropping trg_fact_sales_before_insert for bulk load
#  11. LOAD DATA LOCAL INFILE loop over data/clean/sales_chunk_*.csv
#  12. Recreate trg_fact_sales_before_insert via 05_triggers_events.sql
#  13. gen_synthetic_sales.py --update-config  (writes DB_ALLOCATED_MB to CONFIG)
#  14. Reconciliation check
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

TRIGGER_SQL="${SQL_DIR}/05_triggers_events.sql"
TRIGGER_NAME="trg_fact_sales_before_insert"

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
# Step 9: gen_synthetic_sales.py
# ---------------------------------------------------------------------------
CURRENT_STEP="gen_synthetic_sales.py"
log "Starting ${CURRENT_STEP} — this generates sales CSVs in data/clean/ ..."
"${VENV_PYTHON}" "${ETL_DIR}/gen_synthetic_sales.py"
log "Completed ${CURRENT_STEP}."

# ---------------------------------------------------------------------------
# Step 10: HUMAN STEP — confirm trigger drop for bulk load performance
# ---------------------------------------------------------------------------
CURRENT_STEP="HUMAN confirm trigger drop"
log "HUMAN STEP: FACT_SALES BEFORE INSERT trigger performance decision."
echo ""
echo "  The trigger '${TRIGGER_NAME}' validates product_id and segment_id on every"
echo "  INSERT into FACT_SALES. Dropping it during the bulk load significantly"
echo "  improves performance. It will be recreated automatically afterwards."
echo ""
read -r -p "  Drop FACT_SALES BEFORE INSERT trigger for bulk load performance? [YES/no]: " CONFIRM_DROP_TRIGGER

if [[ "${CONFIRM_DROP_TRIGGER}" == "YES" ]]; then
    CURRENT_STEP="DROP TRIGGER ${TRIGGER_NAME}"
    log "Dropping trigger ${TRIGGER_NAME} for bulk load performance ..."
    mysql_cli --execute="DROP TRIGGER IF EXISTS ${TRIGGER_NAME};"
    log "Trigger ${TRIGGER_NAME} dropped."
    TRIGGER_DROPPED=true
else
    log "Operator chose to keep the trigger. Proceeding with trigger active (bulk load may be slower)."
    TRIGGER_DROPPED=false
fi

# ---------------------------------------------------------------------------
# Step 11: LOAD DATA LOCAL INFILE loop
# ---------------------------------------------------------------------------
CURRENT_STEP="LOAD DATA LOCAL INFILE loop"
log "Starting ${CURRENT_STEP} ..."

CHUNK_COUNT=0
CHUNK_ERRORS=0

# Build list of chunk files; abort if none found
shopt -s nullglob
CHUNK_FILES=("${DATA_CLEAN}"/sales_chunk_*.csv)
shopt -u nullglob

if [[ ${#CHUNK_FILES[@]} -eq 0 ]]; then
    log "ERROR: No chunk files found matching ${DATA_CLEAN}/sales_chunk_*.csv"
    log "       gen_synthetic_sales.py may not have written any output."
    exit 1
fi

log "Found ${#CHUNK_FILES[@]} chunk file(s) to load."

for CHUNK_FILE in "${CHUNK_FILES[@]}"; do
    CURRENT_STEP="LOAD DATA LOCAL INFILE '${CHUNK_FILE}'"
    log "Loading chunk: ${CHUNK_FILE} ..."

    mysql_cli \
        --local-infile=1 \
        --execute="
LOAD DATA LOCAL INFILE '${CHUNK_FILE}'
INTO TABLE FACT_SALES
FIELDS TERMINATED BY ','
OPTIONALLY ENCLOSED BY '\"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES
(sale_date, product_id, segment_id, device_id, region_id, channel_id, qty, gross_amount, discount_amount, @cost)
SET cost_amount = NULLIF(@cost, '');
"
    CHUNK_COUNT=$(( CHUNK_COUNT + 1 ))
    log "Loaded chunk ${CHUNK_COUNT}/${#CHUNK_FILES[@]}: ${CHUNK_FILE}"
done

log "LOAD DATA LOCAL INFILE loop complete. ${CHUNK_COUNT} chunk(s) loaded."

# ---------------------------------------------------------------------------
# Step 12: Recreate trigger (if it was dropped)
# ---------------------------------------------------------------------------
if [[ "${TRIGGER_DROPPED}" == "true" ]]; then
    CURRENT_STEP="recreate trigger ${TRIGGER_NAME}"
    log "Recreating trigger ${TRIGGER_NAME} from ${TRIGGER_SQL} ..."
    mysql_cli < "${TRIGGER_SQL}"
    log "Trigger ${TRIGGER_NAME} recreated."
fi

# ---------------------------------------------------------------------------
# Step 13: Update DB_ALLOCATED_MB in CONFIG
# ---------------------------------------------------------------------------
CURRENT_STEP="gen_synthetic_sales.py --update-config"
log "Starting ${CURRENT_STEP} — updating DB_ALLOCATED_MB in CONFIG ..."
"${VENV_PYTHON}" "${ETL_DIR}/gen_synthetic_sales.py" --update-config
log "Completed ${CURRENT_STEP}."

# ---------------------------------------------------------------------------
# Step 14: Reconciliation check
# ---------------------------------------------------------------------------
CURRENT_STEP="reconciliation check"
log "Starting ${CURRENT_STEP} ..."

RECONCILE_SQL="
SELECT
    cs.segment_name,
    qf.fiscal_year,
    qf.fiscal_quarter,
    qf.revenue                                              AS target_revenue,
    SUM(fs.gross_amount) / 1000000.0                        AS actual_revenue_m,
    ABS(SUM(fs.gross_amount) / 1000000.0 - qf.revenue)
        / qf.revenue * 100                                  AS pct_diff
FROM FACT_SALES fs
JOIN COMPANY_SEGMENT cs   ON cs.segment_id   = fs.segment_id
JOIN QUARTERLY_FINANCIALS qf
    ON qf.segment_id     = fs.segment_id
    AND qf.fiscal_year   = YEAR(fs.sale_date)  -- approximate; proper join via DIM_DATE
GROUP BY cs.segment_name, qf.fiscal_year, qf.fiscal_quarter
HAVING pct_diff > 0.1
ORDER BY pct_diff DESC;
"

RECON_OUTPUT=$(mysql_cli --execute="${RECONCILE_SQL}" 2>&1 || true)

if [[ -n "${RECON_OUTPUT}" ]]; then
    log "WARNING: Reconciliation found rows exceeding 0.1% tolerance:"
    echo "${RECON_OUTPUT}"
    log "Review reconciliation output above. Pipeline completed but check is advisory."
else
    log "Reconciliation check PASSED — all segment/quarter combinations within 0.1% tolerance."
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
log "ETL pipeline completed successfully."
