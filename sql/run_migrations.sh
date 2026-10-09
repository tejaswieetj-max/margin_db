#!/usr/bin/env bash
set -euo pipefail

source .env

export MYSQL_PWD="$ADMIN_PASSWORD"

for file in sql/01_ddl.sql sql/02_roles.sql sql/03_views.sql \
            sql/04_routines.sql sql/05_triggers_events.sql \
            sql/06_seed_reference.sql sql/07_grants.sql; do
  echo "Applying $file ..."
  envsubst '${ADMIN_PASSWORD} ${DEMO_ANALYST_PASSWORD} ${DEMO_ANALYST_ALL_PASSWORD} ${DEMO_ENGINEER_PASSWORD} ${SVC_TELEMETRY_PASSWORD}' < "$file" \
    | mysql -h "$DB_HOST" -P "$DB_PORT" -u "$ADMIN_USER" "$DB_NAME" \
    || { echo "ERROR: $file failed. Halting migration." >&2; exit 1; }
  echo "  OK: $file"
done
echo "All migrations applied successfully."
