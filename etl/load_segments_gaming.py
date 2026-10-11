"""
load_segments_gaming.py — Load Gaming product-line revenue into GAMING_REVENUE_SERIES.

Source:  data/raw/segments.csv
Target:  GAMING_REVENUE_SERIES table in margin_db

CSV columns (from segments.csv):
  fiscal_year, fiscal_quarter, segment, revenue (in million dollars),
  operating_income, source_url, Months

Mapping:
  fiscal_year                  → fiscal_year
  fiscal_quarter               → fiscal_quarter
  revenue (in million dollars) → revenue_m  (already millions USD; insert as-is)
  operating_income             → always NULL (Gaming OI not separately disclosed)
  source_url                   → source_url  (required, non-empty)
  Months                       → months_label

Rows where segment != 'Gaming' are rejected with a per-row log entry and
skipped; they do not abort the pipeline.

Idempotency: INSERT ... ON DUPLICATE KEY UPDATE keyed on
  UNIQUE KEY uq_grs_period (fiscal_year, fiscal_quarter)
  (defined in migration 08 table GAMING_REVENUE_SERIES).

Validation (per-row, never batch-abort):
  - fiscal_year  : int in [2000, 2100]
  - fiscal_quarter: int in {1, 2, 3, 4}
  - segment      : must equal 'Gaming' (case-insensitive strip)
  - revenue (in million dollars): non-negative float
  - source_url   : non-empty string
  - Months       : optional; stored as-is (VARCHAR 40, truncated with warning
                   if > 40 chars)

Requirements: 4.7, 4.12, 21.1, 21.2
"""

import csv
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEGMENTS_CSV_PATH = PROJECT_ROOT / "data" / "raw" / "segments.csv"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logging.Formatter.converter = lambda *args: datetime.now(timezone.utc).timetuple()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FY_MIN, FY_MAX = 2000, 2100
FQ_VALID = {1, 2, 3, 4}
MONTHS_LABEL_MAX = 40   # VARCHAR(40) in GAMING_REVENUE_SERIES

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_gaming_row(row_num: int, row: dict) -> tuple[dict | None, list[dict]]:
    """Validate one row from segments.csv for GAMING_REVENUE_SERIES insertion.

    Returns (validated_dict, rejection_list).
    validated_dict is None if any required field fails.
    Each rejection entry: {row_num, field, expected, actual}
    """
    rejections: list[dict] = []

    # fiscal_year
    try:
        fiscal_year = int(row["fiscal_year"])
        if not (FY_MIN <= fiscal_year <= FY_MAX):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        rejections.append({
            "row_num": row_num,
            "field": "fiscal_year",
            "expected": f"int in [{FY_MIN}, {FY_MAX}]",
            "actual": repr(row.get("fiscal_year")),
        })
        fiscal_year = None

    # fiscal_quarter
    try:
        fiscal_quarter = int(row["fiscal_quarter"])
        if fiscal_quarter not in FQ_VALID:
            raise ValueError
    except (ValueError, KeyError, TypeError):
        rejections.append({
            "row_num": row_num,
            "field": "fiscal_quarter",
            "expected": "int in {1, 2, 3, 4}",
            "actual": repr(row.get("fiscal_quarter")),
        })
        fiscal_quarter = None

    # segment — must be 'Gaming' (case-insensitive)
    segment_raw = row.get("segment", "").strip()
    if segment_raw.lower() != "gaming":
        rejections.append({
            "row_num": row_num,
            "field": "segment",
            "expected": "'Gaming'",
            "actual": repr(segment_raw),
        })

    # revenue (in million dollars) — required, non-negative
    revenue_raw = row.get("revenue (in million dollars)", "").strip()
    try:
        revenue_m = float(revenue_raw)
        if revenue_m < 0:
            raise ValueError("negative")
    except (ValueError, TypeError):
        rejections.append({
            "row_num": row_num,
            "field": "revenue (in million dollars)",
            "expected": "non-negative float (millions USD)",
            "actual": repr(revenue_raw),
        })
        revenue_m = None

    # source_url — required, non-empty
    source_url = row.get("source_url", "").strip()
    if not source_url:
        rejections.append({
            "row_num": row_num,
            "field": "source_url",
            "expected": "non-empty URL string",
            "actual": repr(row.get("source_url")),
        })

    # Months — optional; truncate and warn if > MONTHS_LABEL_MAX chars
    months_label = row.get("Months", "").strip() or None
    if months_label and len(months_label) > MONTHS_LABEL_MAX:
        logger.warning(
            "Row %d: Months value '%s' is %d chars, truncating to %d.",
            row_num, months_label, len(months_label), MONTHS_LABEL_MAX,
        )
        months_label = months_label[:MONTHS_LABEL_MAX]

    if rejections:
        return None, rejections

    return {
        "fiscal_year":    fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "revenue_m":      revenue_m,
        "months_label":   months_label,
        "source_url":     source_url,
    }, []


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_connection(env: dict) -> mysql.connector.MySQLConnection:
    return mysql.connector.connect(
        host=env["DB_HOST"],
        port=int(env.get("DB_PORT", 3306)),
        database=env["DB_NAME"],
        user=env["ADMIN_USER"],
        password=env["ADMIN_PASSWORD"],
        charset="utf8mb4",
        autocommit=False,
    )


# operating_income always NULL — Gaming OI not separately disclosed.
# ON DUPLICATE KEY UPDATE is keyed on UNIQUE KEY uq_grs_period
# (fiscal_year, fiscal_quarter) defined in migration 08.
INSERT_SQL = """
INSERT INTO GAMING_REVENUE_SERIES
    (fiscal_year, fiscal_quarter, revenue_m, operating_income, months_label, source_url)
VALUES
    (%s, %s, %s, NULL, %s, %s)
ON DUPLICATE KEY UPDATE
    revenue_m    = VALUES(revenue_m),
    months_label = VALUES(months_label),
    source_url   = VALUES(source_url)
"""


def upsert_row(
    cursor,
    fiscal_year: int,
    fiscal_quarter: int,
    revenue_m: float,
    months_label: str | None,
    source_url: str,
) -> int:
    cursor.execute(
        INSERT_SQL,
        (fiscal_year, fiscal_quarter, revenue_m, months_label, source_url),
    )
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Core load logic
# ---------------------------------------------------------------------------

def load_gaming_revenue(
    csv_path: Path,
    conn: mysql.connector.MySQLConnection,
) -> dict:
    """Read segments.csv, validate each row, insert Gaming rows into GAMING_REVENUE_SERIES.

    Returns summary dict: rows_read, rows_valid, rows_inserted, rows_rejected.
    """
    rows_read = 0
    rows_valid = 0
    rows_inserted = 0
    all_rejected: list[dict] = []

    required_cols = {
        "fiscal_year", "fiscal_quarter", "segment",
        "revenue (in million dollars)", "source_url",
    }
    # "Months" and "operating_income" are optional; not in required set

    cursor = conn.cursor()
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                logger.error("segments.csv appears empty or has no header.")
                sys.exit(1)
            # Strip BOM / whitespace from header names
            reader.fieldnames = [h.strip().lstrip("\ufeff") for h in reader.fieldnames]
            missing_cols = required_cols - set(reader.fieldnames)
            if missing_cols:
                logger.error(
                    "segments.csv missing required columns: %s (found: %s)",
                    sorted(missing_cols), reader.fieldnames,
                )
                sys.exit(1)

            for row_num, row in enumerate(reader, start=2):
                rows_read += 1
                validated, rejections = validate_gaming_row(row_num, row)

                if rejections:
                    for rej in rejections:
                        logger.warning(
                            "REJECTED row=%d | field=%s | expected=%s | actual=%s",
                            rej["row_num"], rej["field"],
                            rej["expected"], rej["actual"],
                        )
                    all_rejected.extend(rejections)
                    continue

                rows_valid += 1
                try:
                    affected = upsert_row(
                        cursor,
                        validated["fiscal_year"],
                        validated["fiscal_quarter"],
                        validated["revenue_m"],
                        validated["months_label"],
                        validated["source_url"],
                    )
                    rows_inserted += affected
                except mysql.connector.Error as exc:
                    logger.error(
                        "DB error inserting row %d (FY%dQ%d): %s",
                        row_num,
                        validated["fiscal_year"],
                        validated["fiscal_quarter"],
                        exc,
                    )
                    all_rejected.append({
                        "row_num": row_num,
                        "field": "(db insert)",
                        "expected": "successful upsert",
                        "actual": str(exc),
                    })

        conn.commit()

    except mysql.connector.Error as exc:
        logger.error("Database error: %s", exc)
        conn.rollback()
        raise
    finally:
        cursor.close()

    return {
        "rows_read":        rows_read,
        "rows_valid":       rows_valid,
        "rows_inserted":    rows_inserted,
        "rows_rejected":    len(all_rejected),
        "rejected_details": all_rejected,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()

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

    if not SEGMENTS_CSV_PATH.exists():
        logger.error("segments.csv not found at: %s", SEGMENTS_CSV_PATH)
        sys.exit(1)

    logger.info("Loading Gaming revenue from %s", SEGMENTS_CSV_PATH)

    try:
        conn = get_connection(env)
    except mysql.connector.Error as exc:
        logger.error("Cannot connect to database: %s", exc)
        sys.exit(1)

    try:
        summary = load_gaming_revenue(SEGMENTS_CSV_PATH, conn)
    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        conn.close()
        sys.exit(1)
    finally:
        conn.close()

    print("\nGaming revenue load complete:")
    print(f"  Rows read    : {summary['rows_read']}")
    print(f"  Rows valid   : {summary['rows_valid']}")
    print(f"  Rows inserted: {summary['rows_inserted']}  (ON DUPLICATE KEY UPDATE; re-runs safe)")
    print(f"  Rows rejected: {summary['rows_rejected']}")

    if summary["rejected_details"]:
        print("\nRejected row details:")
        for rej in summary["rejected_details"]:
            print(
                f"  row={rej['row_num']}"
                f" | field={rej['field']}"
                f" | expected={rej['expected']}"
                f" | actual={rej['actual']}"
            )

    logger.info(
        "Done. rows_read=%d rows_valid=%d rows_inserted=%d rows_rejected=%d",
        summary["rows_read"], summary["rows_valid"],
        summary["rows_inserted"], summary["rows_rejected"],
    )

    if summary["rows_rejected"] > 0:
        logger.warning(
            "%d rows were rejected. Check log output above for details.",
            summary["rows_rejected"],
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
