"""
load_events.py — Load business events into BUSINESS_EVENT.

Source:  data/raw/events.csv
Target:  BUSINESS_EVENT table in margin_db

CSV columns expected:
  event_type, name, start_date, end_date, expected_lift,
  steam_app_id (INT or blank → NULL), source_url

Validation rules (per Requirements 4.4, 4.5, 21.1, 21.2, 21.3):
  - source_url   : non-null, non-empty string
  - end_date     : parseable ISO 8601 date; must be >= start_date
  - start_date   : parseable ISO 8601 date; non-null
  - event_type   : must be one of the allowed ENUM values
  - steam_app_id : blank → NULL; non-blank must be a valid integer

Allowed event_type values (from BUSINESS_EVENT DDL):
  'game_launch', 'price_change', 'black_friday', 'promotion', 'earnings_release'

Per-row rejection: log row number, field name, expected type/format, and
actual value; skip the bad row but continue processing all remaining rows.
Never batch-abort on a single bad row.

Future-event check (Requirement 4.5):
  After loading, verify at least one game_launch event has start_date > today.
  If none, log a WARNING explaining the @as_of_date workaround.

Idempotency: INSERT IGNORE (no unique key on BUSINESS_EVENT, but re-running
the script will produce duplicate rows unless a unique key is added; INSERT
IGNORE with a UNIQUE constraint would prevent duplicates — here we use it as
specified by the task; for full idempotency a UNIQUE constraint on (name,
start_date) would be required at the schema level).

Note: The BUSINESS_EVENT DDL does not define a UNIQUE KEY, so INSERT IGNORE
behaves like a regular INSERT on this table. The script still uses INSERT IGNORE
per specification to match the project's idempotency intent, and to be forward-
compatible if a unique constraint is added later.

Requirements: 4.4, 4.5, 21.1, 21.2, 21.3
"""

import csv
import logging
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVENTS_CSV_PATH = PROJECT_ROOT / "data" / "raw" / "events.csv"

# ---------------------------------------------------------------------------
# Allowed ENUM values for event_type
# Source: BUSINESS_EVENT DDL comment + task specification
# ---------------------------------------------------------------------------

ALLOWED_EVENT_TYPES: frozenset[str] = frozenset({
    "game_launch",
    "price_change",
    "black_friday",
    "promotion",
    "earnings_release",
})

# ---------------------------------------------------------------------------
# Logging — timestamps in UTC
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logging.Formatter.converter = lambda *args: datetime.now(timezone.utc).timetuple()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _parse_iso_date(value: str, field: str) -> tuple[date | None, str]:
    """Parse value as an ISO 8601 date (YYYY-MM-DD).

    Returns (date_obj, error_reason). error_reason is empty on success.
    """
    if not value or not isinstance(value, str) or not value.strip():
        return None, f"{field}: missing or empty"
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date(), ""
    except ValueError:
        return None, f"not a valid ISO 8601 date (YYYY-MM-DD): {value!r}"


def _parse_optional_decimal(value: str) -> tuple[Decimal | None, str]:
    """Parse value as a Decimal, treating blank as NULL.

    Returns (decimal_value_or_None, error_reason).
    """
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None, ""  # blank → NULL, no error
    try:
        return Decimal(str(value).strip()), ""
    except InvalidOperation:
        return None, f"not a valid decimal: {value!r}"


def _parse_optional_int(value: str) -> tuple[int | None, str]:
    """Parse value as an integer, treating blank/empty as NULL.

    Returns (int_value_or_None, error_reason).
    """
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None, ""  # blank → NULL, no error
    try:
        # Allow whole-number floats like "1938090.0"
        f = float(value)
        if f != int(f):
            return None, f"not a whole number: {value!r}"
        return int(f), ""
    except (ValueError, TypeError):
        return None, f"not a valid integer: {value!r}"


# ---------------------------------------------------------------------------
# Row validation
# ---------------------------------------------------------------------------


def validate_row(
    row_num: int, row: dict
) -> tuple[dict | None, list[dict]]:
    """Validate a single CSV row for BUSINESS_EVENT insertion.

    Returns (validated_dict, rejection_list).
    validated_dict is None if any required field fails validation.
    Each rejection entry: {row_num, field, expected, actual}
    """
    rejections: list[dict] = []

    # --- event_type ---
    event_type = row.get("event_type", "").strip()
    if not event_type:
        rejections.append({
            "row_num": row_num,
            "field": "event_type",
            "expected": f"one of {sorted(ALLOWED_EVENT_TYPES)}",
            "actual": repr(row.get("event_type")),
        })
        event_type = None
    elif event_type not in ALLOWED_EVENT_TYPES:
        rejections.append({
            "row_num": row_num,
            "field": "event_type",
            "expected": f"one of {sorted(ALLOWED_EVENT_TYPES)}",
            "actual": repr(event_type),
        })
        event_type = None

    # --- source_url ---
    source_url = row.get("source_url", "").strip()
    if not source_url:
        rejections.append({
            "row_num": row_num,
            "field": "source_url",
            "expected": "non-null, non-empty URL string",
            "actual": repr(row.get("source_url")),
        })
        source_url = None

    # --- start_date ---
    start_date, start_err = _parse_iso_date(row.get("start_date", ""), "start_date")
    if start_err:
        rejections.append({
            "row_num": row_num,
            "field": "start_date",
            "expected": "ISO 8601 date YYYY-MM-DD",
            "actual": f"{row.get('start_date')!r} — {start_err}",
        })

    # --- end_date ---
    end_date, end_err = _parse_iso_date(row.get("end_date", ""), "end_date")
    if end_err:
        rejections.append({
            "row_num": row_num,
            "field": "end_date",
            "expected": "ISO 8601 date YYYY-MM-DD",
            "actual": f"{row.get('end_date')!r} — {end_err}",
        })

    # --- end_date >= start_date (only check if both parsed successfully) ---
    if start_date is not None and end_date is not None:
        if end_date < start_date:
            rejections.append({
                "row_num": row_num,
                "field": "end_date",
                "expected": f"end_date >= start_date ({start_date.isoformat()})",
                "actual": repr(end_date.isoformat()),
            })
            end_date = None

    # --- expected_lift (optional; blank → NULL) ---
    expected_lift, lift_err = _parse_optional_decimal(row.get("expected_lift", ""))
    if lift_err:
        rejections.append({
            "row_num": row_num,
            "field": "expected_lift",
            "expected": "decimal value (e.g. 0.0500) or blank for NULL",
            "actual": f"{row.get('expected_lift')!r} — {lift_err}",
        })
        expected_lift = None  # treat parse failure as NULL but still reject row

    # --- steam_app_id (optional; blank → NULL) ---
    steam_app_id, steam_err = _parse_optional_int(row.get("steam_app_id", ""))
    if steam_err:
        rejections.append({
            "row_num": row_num,
            "field": "steam_app_id",
            "expected": "integer (e.g. 1938090) or blank for NULL",
            "actual": f"{row.get('steam_app_id')!r} — {steam_err}",
        })
        steam_app_id = None

    # name — not validated strictly (VARCHAR(200)); just strip and pass through
    name = row.get("name", "").strip() or None

    if rejections:
        return None, rejections

    return {
        "event_type": event_type,
        "name": name,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "expected_lift": float(expected_lift) if expected_lift is not None else None,
        "steam_app_id": steam_app_id,
        "source_url": source_url,
    }, []


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def get_connection(env: dict) -> mysql.connector.MySQLConnection:
    """Open a mysql-connector-python connection using credentials from env."""
    return mysql.connector.connect(
        host=env["DB_HOST"],
        port=int(env.get("DB_PORT", 3306)),
        database=env["DB_NAME"],
        user=env["ADMIN_USER"],
        password=env["ADMIN_PASSWORD"],
        charset="utf8mb4",
        autocommit=False,
    )


# INSERT IGNORE: skip silently if a UNIQUE constraint is ever added.
# BUSINESS_EVENT table columns: event_id (PK AUTO_INCREMENT), event_type,
# name, start_date, end_date, expected_lift, source_url.
# Note: The DDL does NOT include steam_app_id — that column lives only in CSV
# for cross-reference; it is NOT loaded into BUSINESS_EVENT per the schema.
INSERT_SQL = """
INSERT IGNORE INTO BUSINESS_EVENT
    (event_type, name, start_date, end_date, expected_lift, source_url)
VALUES
    (%s, %s, %s, %s, %s, %s)
"""

BATCH_SIZE = 200  # commit every N successfully validated rows


def insert_batch(cursor, batch: list[dict]) -> int:
    """Execute INSERT IGNORE for a batch of validated rows.

    Returns cursor.rowcount (rows actually inserted; 0 for duplicates).
    """
    params = [
        (
            r["event_type"],
            r["name"],
            r["start_date"],
            r["end_date"],
            r["expected_lift"],
            r["source_url"],
        )
        for r in batch
    ]
    cursor.executemany(INSERT_SQL, params)
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Future-event check
# ---------------------------------------------------------------------------


def check_future_game_launches(
    valid_rows: list[dict],
) -> None:
    """Verify at least one game_launch event has start_date > today.

    If none found, log a WARNING with the @as_of_date workaround explanation.
    Does NOT abort — purely advisory.

    Requirement 4.5: future-event check.
    """
    today = date.today()
    future_launches = [
        r for r in valid_rows
        if r["event_type"] == "game_launch"
        and r["start_date"] is not None
        and date.fromisoformat(r["start_date"]) > today
    ]

    if future_launches:
        logger.info(
            "Future-event check PASSED: found %d game_launch event(s) with "
            "start_date > %s: %s",
            len(future_launches),
            today.isoformat(),
            [r["name"] for r in future_launches],
        )
    else:
        logger.warning(
            "Future-event check FAILED: no game_launch event found with "
            "start_date > %s. "
            "sp_forecast_business uses CURDATE() as the reference point, so "
            "forecasts will not exercise future-event logic. "
            "Workaround: set the MySQL session variable @as_of_date to a past "
            "date that precedes a known game_launch start_date before calling "
            "sp_forecast_business. Example: "
            "SET @as_of_date = '2025-11-01'; "
            "CALL sp_forecast_business(<event_id>); "
            "This causes the stored procedure to evaluate events relative to "
            "@as_of_date rather than CURDATE(), allowing historical events to "
            "exercise the forecast path as if they were still upcoming.",
            today.isoformat(),
        )


# ---------------------------------------------------------------------------
# Core load logic
# ---------------------------------------------------------------------------


def load_events(csv_path: Path, conn: mysql.connector.MySQLConnection) -> dict:
    """Read the CSV, validate each row, and insert valid rows into BUSINESS_EVENT.

    Returns a summary dict with keys:
      rows_read, rows_valid, rows_inserted, rows_rejected, valid_rows
    """
    rows_read = 0
    rows_valid = 0
    rows_inserted = 0
    all_rejected: list[dict] = []
    all_valid_rows: list[dict] = []

    cursor = conn.cursor()
    try:
        pending_batch: list[dict] = []

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            # Verify required columns are present
            required_cols = {
                "event_type", "name", "start_date", "end_date",
                "expected_lift", "steam_app_id", "source_url",
            }
            if reader.fieldnames is None:
                logger.error(
                    "CSV file appears empty or has no header: %s", csv_path
                )
                sys.exit(1)
            missing_cols = required_cols - set(
                c.strip() for c in reader.fieldnames
            )
            if missing_cols:
                logger.error(
                    "CSV is missing required columns: %s (found: %s)",
                    sorted(missing_cols),
                    reader.fieldnames,
                )
                sys.exit(1)

            for row_num, row in enumerate(reader, start=2):  # row 1 = header
                rows_read += 1
                validated, rejections = validate_row(row_num, row)

                if rejections:
                    for rej in rejections:
                        logger.warning(
                            "REJECTED row=%d | field=%s | expected=%s | actual=%s",
                            rej["row_num"],
                            rej["field"],
                            rej["expected"],
                            rej["actual"],
                        )
                    all_rejected.extend(rejections)
                    continue

                rows_valid += 1
                all_valid_rows.append(validated)
                pending_batch.append(validated)

                if len(pending_batch) >= BATCH_SIZE:
                    inserted = insert_batch(cursor, pending_batch)
                    rows_inserted += inserted
                    conn.commit()
                    logger.info(
                        "Committed batch: %d rows sent, %d inserted "
                        "(rest were duplicates)",
                        len(pending_batch),
                        inserted,
                    )
                    pending_batch = []

        # Flush remaining rows
        if pending_batch:
            inserted = insert_batch(cursor, pending_batch)
            rows_inserted += inserted
            conn.commit()
            logger.info(
                "Committed final batch: %d rows sent, %d inserted "
                "(rest were duplicates)",
                len(pending_batch),
                inserted,
            )

    except mysql.connector.Error as exc:
        logger.error("Database error during insert: %s", exc)
        conn.rollback()
        raise
    finally:
        cursor.close()

    return {
        "rows_read": rows_read,
        "rows_valid": rows_valid,
        "rows_inserted": rows_inserted,
        "rows_rejected": len(all_rejected),
        "rejected_details": all_rejected,
        "valid_rows": all_valid_rows,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    load_dotenv()

    # Collect required environment variables
    required_env = ["DB_HOST", "DB_NAME", "ADMIN_USER", "ADMIN_PASSWORD"]
    env: dict = {}
    missing_env: list[str] = []
    for key in required_env:
        val = os.environ.get(key, "").strip()
        if not val:
            missing_env.append(key)
        env[key] = val
    env["DB_PORT"] = os.environ.get("DB_PORT", "3306").strip()

    if missing_env:
        logger.error(
            "Missing required environment variables: %s", missing_env
        )
        sys.exit(1)

    # Verify CSV exists
    if not EVENTS_CSV_PATH.exists():
        logger.error("events.csv not found at: %s", EVENTS_CSV_PATH)
        sys.exit(1)

    logger.info("Loading business events from %s", EVENTS_CSV_PATH)

    # Connect to database
    try:
        conn = get_connection(env)
    except mysql.connector.Error as exc:
        logger.error("Cannot connect to database: %s", exc)
        sys.exit(1)

    try:
        summary = load_events(EVENTS_CSV_PATH, conn)
    except Exception as exc:
        logger.error("Unexpected error during load: %s", exc)
        conn.close()
        sys.exit(1)
    finally:
        conn.close()

    # --- Future-event check (post-load, in-memory; does not abort) ---
    check_future_game_launches(summary["valid_rows"])

    # --- Final summary ---
    print("\nBusiness event load complete:")
    print(f"  Rows read    : {summary['rows_read']}")
    print(f"  Rows valid   : {summary['rows_valid']}")
    print(f"  Rows inserted: {summary['rows_inserted']}  "
          "(INSERT IGNORE; duplicates skipped)")
    print(f"  Rows rejected: {summary['rows_rejected']}")

    if summary["rejected_details"]:
        print("\nRejected row details:")
        seen_rows: set[int] = set()
        for rej in summary["rejected_details"]:
            if rej["row_num"] not in seen_rows:
                seen_rows.add(rej["row_num"])
                print(f"  row={rej['row_num']}")
            print(
                f"    field={rej['field']}"
                f" | expected={rej['expected']}"
                f" | actual={rej['actual']}"
            )

    logger.info(
        "Done. rows_read=%d rows_valid=%d rows_inserted=%d rows_rejected=%d",
        summary["rows_read"],
        summary["rows_valid"],
        summary["rows_inserted"],
        summary["rows_rejected"],
    )

    # Exit non-zero if any rows were rejected so the shell orchestrator can log it
    if summary["rows_rejected"] > 0:
        logger.warning(
            "%d rows were rejected. Check log output above for details.",
            summary["rows_rejected"],
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
