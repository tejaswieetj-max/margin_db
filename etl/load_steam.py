"""
load_steam.py — Load Steam player-count data into STEAM_PLAYER.

Source:  data/raw/steam_players.csv
Target:  STEAM_PLAYER table in margin_db

CSV columns expected:
  app_id, obs_date, avg_players, peak_players

Validation rules (Phase B):
  - app_id        : non-null integer
  - obs_date      : ISO 8601 date (YYYY-MM-DD) AND must be the 1st of the month
  - avg_players   : non-negative decimal (stored as DECIMAL(12,2))
  - peak_players  : non-negative integer

Per-row rejection: log row number, field name, expected type/format, and
actual value; skip the bad row but continue processing all remaining rows.

Idempotency: INSERT IGNORE (UNIQUE KEY on (app_id, obs_date) in STEAM_PLAYER).

This script loads STEAM_PLAYER rows ONLY.
EVENT_WINDOW_MONTH computation is handled separately by compute_event_windows.py.

Requirements: 4.2, 21.1, 21.2
"""

import csv
import logging
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STEAM_CSV_PATH = PROJECT_ROOT / "data" / "raw" / "steam_players.csv"

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

def _is_iso_date_first_of_month(value: str) -> tuple[bool, str]:
    """Return (valid, reason) for an obs_date value.

    Valid requires:
      1. Non-empty string
      2. Parseable as YYYY-MM-DD
      3. Day component equals 1 (first of the month)
    """
    if not value or not isinstance(value, str):
        return False, "empty or non-string"
    try:
        dt = datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        return False, f"not a valid ISO 8601 date (YYYY-MM-DD): {value!r}"
    if dt.day != 1:
        return False, f"day is {dt.day}, must be 1 (first of the month)"
    return True, ""


def _parse_nonneg_decimal(value: str) -> tuple[Decimal | None, str]:
    """Parse value as a non-negative Decimal.

    Returns (decimal_value, error_reason).
    error_reason is empty string on success.
    """
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None, "missing or empty"
    try:
        d = Decimal(str(value).strip())
    except InvalidOperation:
        return None, f"not a valid decimal: {value!r}"
    if d < 0:
        return None, f"negative value: {d}"
    return d, ""


def _parse_nonneg_int(value: str) -> tuple[int | None, str]:
    """Parse value as a non-negative integer.

    Returns (int_value, error_reason).
    error_reason is empty string on success.
    """
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None, "missing or empty"
    try:
        # Allow floats that are whole numbers (e.g. "262875.0")
        f = float(value)
        if f != int(f):
            return None, f"not a whole number: {value!r}"
        i = int(f)
    except (ValueError, TypeError):
        return None, f"not a valid integer: {value!r}"
    if i < 0:
        return None, f"negative value: {i}"
    return i, ""


def _parse_int(value: str) -> tuple[int | None, str]:
    """Parse value as an integer (used for app_id).

    Returns (int_value, error_reason).
    """
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None, "missing or empty"
    try:
        return int(str(value).strip()), ""
    except (ValueError, TypeError):
        return None, f"not a valid integer: {value!r}"


def validate_row(row_num: int, row: dict) -> tuple[dict | None, list[dict]]:
    """Validate a single CSV row.

    Returns (validated_dict, rejection_list).
    validated_dict is None if any field fails validation.
    rejection_list entries: {row_num, field, expected, actual}
    """
    rejections: list[dict] = []

    # --- app_id ---
    app_id, err = _parse_int(row.get("app_id", ""))
    if err:
        rejections.append({
            "row_num": row_num,
            "field": "app_id",
            "expected": "non-null integer",
            "actual": repr(row.get("app_id")),
        })

    # --- obs_date ---
    obs_date_raw = row.get("obs_date", "").strip()
    valid_date, date_err = _is_iso_date_first_of_month(obs_date_raw)
    if not valid_date:
        rejections.append({
            "row_num": row_num,
            "field": "obs_date",
            "expected": "ISO 8601 date YYYY-MM-DD where DD=01 (first of the month)",
            "actual": f"{obs_date_raw!r} — {date_err}",
        })
        obs_date_raw = None  # mark invalid

    # --- avg_players ---
    avg_players, avg_err = _parse_nonneg_decimal(row.get("avg_players", ""))
    if avg_err:
        rejections.append({
            "row_num": row_num,
            "field": "avg_players",
            "expected": "non-negative decimal (DECIMAL(12,2))",
            "actual": f"{row.get('avg_players')!r} — {avg_err}",
        })

    # --- peak_players ---
    peak_players, peak_err = _parse_nonneg_int(row.get("peak_players", ""))
    if peak_err:
        rejections.append({
            "row_num": row_num,
            "field": "peak_players",
            "expected": "non-negative integer",
            "actual": f"{row.get('peak_players')!r} — {peak_err}",
        })

    if rejections:
        return None, rejections

    return {
        "app_id": app_id,
        "obs_date": obs_date_raw,
        "avg_players": float(avg_players),   # mysql-connector accepts float for DECIMAL
        "peak_players": peak_players,
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


# INSERT IGNORE: skip silently if (app_id, obs_date) already exists (idempotency).
INSERT_SQL = """
INSERT IGNORE INTO STEAM_PLAYER (app_id, obs_date, avg_players, peak_players)
VALUES (%s, %s, %s, %s)
"""

BATCH_SIZE = 500  # commit every N successfully validated rows


def insert_batch(cursor, batch: list[dict]) -> int:
    """Execute INSERT IGNORE for a batch of validated rows.

    Returns number of rows actually inserted (0 for duplicates, 1 for new).
    """
    params = [
        (r["app_id"], r["obs_date"], r["avg_players"], r["peak_players"])
        for r in batch
    ]
    cursor.executemany(INSERT_SQL, params)
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Core load logic
# ---------------------------------------------------------------------------

def load_steam_players(csv_path: Path, conn: mysql.connector.MySQLConnection) -> dict:
    """Read the CSV, validate each row, and insert valid rows into STEAM_PLAYER.

    Returns a summary dict with keys:
      rows_read, rows_valid, rows_inserted, rows_rejected
    """
    rows_read = 0
    rows_valid = 0
    rows_inserted = 0
    all_rejected: list[dict] = []

    cursor = conn.cursor()
    try:
        pending_batch: list[dict] = []

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            # Verify required columns are present
            required_cols = {"app_id", "obs_date", "avg_players", "peak_players"}
            if reader.fieldnames is None:
                logger.error("CSV file appears empty or has no header: %s", csv_path)
                sys.exit(1)
            missing_cols = required_cols - set(reader.fieldnames)
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
                pending_batch.append(validated)

                if len(pending_batch) >= BATCH_SIZE:
                    inserted = insert_batch(cursor, pending_batch)
                    rows_inserted += inserted
                    conn.commit()
                    logger.info(
                        "Committed batch: %d rows sent, %d inserted (rest were duplicates)",
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
                "Committed final batch: %d rows sent, %d inserted (rest were duplicates)",
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
        logger.error("Missing required environment variables: %s", missing_env)
        sys.exit(1)

    # Verify CSV exists
    if not STEAM_CSV_PATH.exists():
        logger.error("steam_players.csv not found at: %s", STEAM_CSV_PATH)
        sys.exit(1)

    logger.info("Loading Steam player data from %s", STEAM_CSV_PATH)

    # Connect to database
    try:
        conn = get_connection(env)
    except mysql.connector.Error as exc:
        logger.error("Cannot connect to database: %s", exc)
        sys.exit(1)

    try:
        summary = load_steam_players(STEAM_CSV_PATH, conn)
    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        conn.close()
        sys.exit(1)
    finally:
        conn.close()

    # --- Final summary ---
    print(f"\nSteam player load complete:")
    print(f"  Rows read    : {summary['rows_read']}")
    print(f"  Rows valid   : {summary['rows_valid']}")
    print(f"  Rows inserted: {summary['rows_inserted']}  (INSERT IGNORE; duplicates skipped)")
    print(f"  Rows rejected: {summary['rows_rejected']}")

    if summary["rejected_details"]:
        print(f"\nRejected row details:")
        for rej in summary["rejected_details"]:
            print(
                f"  row={rej['row_num']}"
                f" | field={rej['field']}"
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

    # Exit non-zero if any rows were rejected, so the shell orchestrator can log it
    if summary["rows_rejected"] > 0:
        logger.warning(
            "%d rows were rejected. Check log output above for details.",
            summary["rows_rejected"],
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
