#!/usr/bin/env bash
# tests/run_role_tests.sh
#
# Role-based access control tests for margin_db.
# Runs entirely via MySQL CLI — no backend required.
#
# Requirements: 2.10, 18.1
#
# Usage: bash tests/run_role_tests.sh

set -euo pipefail

# ---------------------------------------------------------------------------
# Source .env from the project root (one level up from tests/)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  echo "ERROR: .env not found at $ROOT_DIR/.env" >&2
  exit 1
fi

# shellcheck source=../.env
set +u
# Use a subshell-safe dotenv load that handles comments and blank lines
while IFS= read -r line || [[ -n "$line" ]]; do
  # Skip comments and blank lines
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ -z "${line// }" ]] && continue
  # Export only lines that look like KEY=VALUE
  if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
    export "${line?}"
  fi
done < "$ROOT_DIR/.env"
set -u

# ---------------------------------------------------------------------------
# Required variables (fail fast if any are missing)
# ---------------------------------------------------------------------------
: "${DB_HOST:?DB_HOST not set in .env}"
: "${DB_PORT:?DB_PORT not set in .env}"
: "${DB_NAME:?DB_NAME not set in .env}"
: "${ADMIN_USER:?ADMIN_USER not set in .env}"
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD not set in .env}"
: "${ANALYST_DEMO_USERNAME:?ANALYST_DEMO_USERNAME not set in .env}"
: "${ANALYST_ALL_USERNAME:?ANALYST_ALL_USERNAME not set in .env}"
: "${ENGINEER_DEMO_USERNAME:?ENGINEER_DEMO_USERNAME not set in .env}"
: "${DEMO_ANALYST_PASSWORD:?DEMO_ANALYST_PASSWORD not set in .env}"
: "${DEMO_ANALYST_ALL_PASSWORD:?DEMO_ANALYST_ALL_PASSWORD not set in .env}"
: "${DEMO_ENGINEER_PASSWORD:?DEMO_ENGINEER_PASSWORD not set in .env}"

# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------
PASS=0
FAIL=0

# ---------------------------------------------------------------------------
# Helper: run_mysql_as <user> <password> <sql>
#   Runs a query via MySQL CLI using MYSQL_PWD to avoid password on CLI.
#   Captures combined stdout+stderr so callers can inspect error strings.
# ---------------------------------------------------------------------------
run_mysql_as() {
  local user="$1"
  local pass="$2"
  local sql="$3"
  MYSQL_PWD="$pass" mysql \
    -h "$DB_HOST" \
    -P "$DB_PORT" \
    -u "$user" \
    "$DB_NAME" \
    2>&1 \
    -e "$sql"
}

# ---------------------------------------------------------------------------
# assert_denied <label> <user> <password> <sql>
#   Asserts that running <sql> as <user> produces ERROR 1142 (access denied).
#   Prints PASS or FAIL.
# ---------------------------------------------------------------------------
assert_denied() {
  local label="$1"
  local user="$2"
  local pass="$3"
  local sql="$4"

  # Capture both stdout and stderr; mysql exits non-zero on error, so allow failure
  local result
  result=$(MYSQL_PWD="$pass" mysql \
    -h "$DB_HOST" \
    -P "$DB_PORT" \
    -u "$user" \
    "$DB_NAME" \
    2>&1 \
    -e "$sql" || true)

  if echo "$result" | grep -q "ERROR 1142"; then
    echo "PASS  $label"
    PASS=$((PASS + 1))
  else
    echo "FAIL  $label"
    echo "      Expected ERROR 1142 but got: $result"
    FAIL=$((FAIL + 1))
  fi
}

# ---------------------------------------------------------------------------
# assert_row_count <label> <user> <password> <query> <expected_count>
#   Runs <query> as <user>; counts the rows returned; asserts == <expected_count>.
#   <query> should be a SELECT that returns one column per row.
#   Prints PASS or FAIL.
# ---------------------------------------------------------------------------
assert_row_count() {
  local label="$1"
  local user="$2"
  local pass="$3"
  local query="$4"
  local expected="$5"

  local actual
  actual=$(MYSQL_PWD="$pass" mysql \
    -h "$DB_HOST" \
    -P "$DB_PORT" \
    -u "$user" \
    "$DB_NAME" \
    --skip-column-names \
    -e "$query" 2>&1) || true

  # Trim whitespace
  actual="${actual// /}"
  actual="${actual//$'\t'/}"
  actual="${actual//$'\n'/}"
  actual="${actual//$'\r'/}"

  if [[ "$actual" == "$expected" ]]; then
    echo "PASS  $label (count=$actual)"
    PASS=$((PASS + 1))
  else
    echo "FAIL  $label"
    echo "      Expected count=$expected but got: $actual"
    FAIL=$((FAIL + 1))
  fi
}

# ---------------------------------------------------------------------------
# Main test suite
# ---------------------------------------------------------------------------
echo "============================================"
echo "  margin_db Role-Based Access Control Tests"
echo "============================================"
echo ""

echo "--- Denial checks: analyst_demo ---"

# 1. analyst_demo must NOT be able to read QUERY_PLAN_NODE
assert_denied \
  "analyst_demo denied SELECT on QUERY_PLAN_NODE" \
  "$ANALYST_DEMO_USERNAME" "$DEMO_ANALYST_PASSWORD" \
  "SELECT 1 FROM QUERY_PLAN_NODE LIMIT 1"

# 2. analyst_demo must NOT be able to read INDEX_RECOMMENDATION
assert_denied \
  "analyst_demo denied SELECT on INDEX_RECOMMENDATION" \
  "$ANALYST_DEMO_USERNAME" "$DEMO_ANALYST_PASSWORD" \
  "SELECT 1 FROM INDEX_RECOMMENDATION LIMIT 1"

# 3. analyst_demo must NOT be able to read FACT_SALES directly
assert_denied \
  "analyst_demo denied SELECT on FACT_SALES" \
  "$ANALYST_DEMO_USERNAME" "$DEMO_ANALYST_PASSWORD" \
  "SELECT 1 FROM FACT_SALES LIMIT 1"

echo ""
echo "--- Denial checks: engineer_demo ---"

# 4. engineer_demo must NOT be able to read FACT_SALES
assert_denied \
  "engineer_demo denied SELECT on FACT_SALES" \
  "$ENGINEER_DEMO_USERNAME" "$DEMO_ENGINEER_PASSWORD" \
  "SELECT 1 FROM FACT_SALES LIMIT 1"

# 5. engineer_demo must NOT be able to read ORDERS
assert_denied \
  "engineer_demo denied SELECT on ORDERS" \
  "$ENGINEER_DEMO_USERNAME" "$DEMO_ENGINEER_PASSWORD" \
  "SELECT 1 FROM ORDERS LIMIT 1"

# 6. engineer_demo must NOT be able to read QUARTERLY_FINANCIALS
assert_denied \
  "engineer_demo denied SELECT on QUARTERLY_FINANCIALS" \
  "$ENGINEER_DEMO_USERNAME" "$DEMO_ENGINEER_PASSWORD" \
  "SELECT 1 FROM QUARTERLY_FINANCIALS LIMIT 1"

echo ""
echo "--- Row-count check: analyst_demo v_sales shows only Gaming product_line rows ---"

# 7a. Compute the Gaming-product-line row count via admin (ground truth).
#     analyst_demo is mapped to MPC segment with product_line='Gaming'.
#     v_sales filters on (segment_id IN USM AND product_line matches USM.product_line).
gaming_count=$(MYSQL_PWD="$ADMIN_PASSWORD" mysql \
  -h "$DB_HOST" \
  -P "$DB_PORT" \
  -u "$ADMIN_USER" \
  "$DB_NAME" \
  --skip-column-names \
  -e "SELECT COUNT(*)
      FROM FACT_SALES fs
      JOIN PRODUCT p ON fs.product_id = p.product_id
      WHERE p.product_line = 'Gaming'" 2>&1) || true

gaming_count="${gaming_count// /}"
gaming_count="${gaming_count//$'\t'/}"
gaming_count="${gaming_count//$'\n'/}"
gaming_count="${gaming_count//$'\r'/}"

if ! [[ "$gaming_count" =~ ^[0-9]+$ ]]; then
  echo "FAIL  Could not retrieve Gaming product_line row count from admin; got: $gaming_count"
  FAIL=$((FAIL + 1))
else
  # 7b. analyst_demo sees the same count through v_sales
  assert_row_count \
    "analyst_demo v_sales matches Gaming product_line rows (expected=$gaming_count)" \
    "$ANALYST_DEMO_USERNAME" "$DEMO_ANALYST_PASSWORD" \
    "SELECT COUNT(*) FROM v_sales" \
    "$gaming_count"
fi

echo ""
echo "--- Denial checks: engineer_demo GAMING_REVENUE_SERIES ---"

# 8. engineer_demo must NOT be able to read GAMING_REVENUE_SERIES
assert_denied \
  "engineer_demo denied SELECT on GAMING_REVENUE_SERIES" \
  "$ENGINEER_DEMO_USERNAME" "$DEMO_ENGINEER_PASSWORD" \
  "SELECT 1 FROM GAMING_REVENUE_SERIES LIMIT 1"

echo ""
echo "--- analyst_demo cannot see non-Gaming rows in v_financials ---"

# 9. analyst_demo is mapped to MPC; v_financials should only return MPC rows.
#    Verify no PBP or IC rows appear.
pbp_count=$(MYSQL_PWD="$DEMO_ANALYST_PASSWORD" mysql \
  -h "$DB_HOST" \
  -P "$DB_PORT" \
  -u "$ANALYST_DEMO_USERNAME" \
  "$DB_NAME" \
  --skip-column-names \
  -e "SELECT COUNT(*) FROM v_financials
      WHERE segment_name NOT IN ('More Personal Computing')" 2>&1) || true
pbp_count="${pbp_count// /}"
pbp_count="${pbp_count//$'\t'/}"
pbp_count="${pbp_count//$'\n'/}"
pbp_count="${pbp_count//$'\r'/}"

if [[ "$pbp_count" == "0" ]]; then
  echo "PASS  analyst_demo v_financials shows MPC rows only (no PBP/IC)"
  PASS=$((PASS + 1))
else
  echo "FAIL  analyst_demo v_financials returned $pbp_count non-MPC rows (expected 0)"
  FAIL=$((FAIL + 1))
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "============================================"
echo "  Results: $PASS passed, $FAIL failed"
echo "============================================"

if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
