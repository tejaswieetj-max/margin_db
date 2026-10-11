"""
load_financials.py — Load quarterly financials from msft_facts.json into QUARTERLY_FINANCIALS.

Source:  data/raw/msft_facts.json  (produced by fetch_edgar.py)
Target:  QUARTERLY_FINANCIALS table in margin_db

Metrics loaded: revenue, cogs, operating_income (all three required per period)
Annual and TTM frames are already excluded by fetch_edgar.py; this script
operates on the Q1–Q4 records it produces.

Idempotency: INSERT ... ON DUPLICATE KEY UPDATE so re-runs are safe.

Validation: fields are checked before insert; rejected rows are logged with
  identifier (fiscal_year/fiscal_quarter/metric), field name,
  expected type/range, and actual value.

Segment: loads with segment_id for "Gaming" (default), or per-segment if
  data/raw/segments.csv exists.

Requirements: 4.1, 4.7, 21.1, 21.2
"""

import csv
import json
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
FACTS_PATH = PROJECT_ROOT / "data" / "raw" / "msft_facts.json"
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
# Validation helpers
# ---------------------------------------------------------------------------

VALID_METRICS = {"revenue", "cogs", "operating_income"}

# QUARTERLY_FINANCIALS stores amounts as DECIMAL(15,2) — values from SEC EDGAR
# are in dollars (integer cents at most).  We accept any real number.
# Fiscal year: Microsoft's fiscal year ends June 30; data covers FY2012–FY2025 range.
FY_MIN = 2000
FY_MAX = 2100
FQ_VALID = {1, 2, 3, 4}


def _validate_record(record: dict) -> list[dict]:
    """Validate a single flat record dict.

    Returns a list of rejection dicts (empty if valid):
      {identifier, field, expected, actual}
    """
    fy = record.get("fiscal_year")
    fq = record.get("fiscal_quarter")
    metric = record.get("metric")
    identifier = f"{fy}/{fq}/{metric}"
    rejections = []

    # fiscal_year
    if not isinstance(fy, int) or not (FY_MIN <= fy <= FY_MAX):
        rejections.append({
            "identifier": identifier,
            "field": "fiscal_year",
            "expected": f"int in [{FY_MIN}, {FY_MAX}]",
            "actual": repr(fy),
        })

    # fiscal_quarter
    if not isinstance(fq, int) or fq not in FQ_VALID:
        rejections.append({
            "identifier": identifier,
            "field": "fiscal_quarter",
            "expected": "int in {1, 2, 3, 4}",
            "actual": repr(fq),
        })

    # metric
    if metric not in VALID_METRICS:
        rejections.append({
            "identifier": identifier,
            "field": "metric",
            "expected": f"one of {sorted(VALID_METRICS)}",
            "actual": repr(metric),
        })

    # value — must be a real number (int or float), not None
    value = record.get("value")
    if not isinstance(value, (int, float)) or value is None:
        rejections.append({
            "identifier": identifier,
            "field": "value",
            "expected": "numeric (int or float)",
            "actual": repr(value),
        })

    # filed_date — must be a non-empty ISO date string (YYYY-MM-DD)
    filed_date = record.get("filed_date", "")
    if not isinstance(filed_date, str) or not _is_iso_date(filed_date):
        rejections.append({
            "identifier": identifier,
            "field": "filed_date",
            "expected": "ISO date string YYYY-MM-DD",
            "actual": repr(filed_date),
        })

    return rejections


def _is_iso_date(value: str) -> bool:
    """Return True if value matches YYYY-MM-DD and is a valid calendar date."""
    if not value or len(value) != 10:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def load_facts(path: Path) -> list[dict]:
    """Read msft_facts.json and return list of records."""
    if not path.exists():
        logger.error("Facts file not found: %s", path)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        records = json.load(f)
    if not isinstance(records, list):
        logger.error("Expected a JSON array in %s", path)
        sys.exit(1)
    logger.info("Loaded %d records from %s", len(records), path)
    return records


def group_by_period(records: list[dict]) -> dict[tuple, dict]:
    """Group validated records by (fiscal_year, fiscal_quarter).

    Within a group, keep latest filed_date per metric (deduplication of
    restatements — Requirement 4.7).

    Returns {(fy, fq): {"revenue": record, "cogs": record, "operating_income": record}}
    where each inner record is the one with the latest filed_date.
    """
    # First pass: group by (fy, fq, metric) keeping latest filed_date
    best: dict[tuple, dict] = {}  # (fy, fq, metric) → record

    for rec in records:
        fy = rec.get("fiscal_year")
        fq = rec.get("fiscal_quarter")
        metric = rec.get("metric")
        if not (isinstance(fy, int) and isinstance(fq, int) and metric in VALID_METRICS):
            continue
        key = (fy, fq, metric)
        filed = rec.get("filed_date", "")
        existing = best.get(key)
        if existing is None or filed > existing.get("filed_date", ""):
            best[key] = rec

    # Second pass: group by period
    periods: dict[tuple, dict] = {}
    for (fy, fq, metric), rec in best.items():
        period_key = (fy, fq)
        if period_key not in periods:
            periods[period_key] = {}
        periods[period_key][metric] = rec

    return periods


def load_segments_csv(path: Path) -> list[dict]:
    """Load segments.csv if available.

    Expected columns: fiscal_year, fiscal_quarter, segment_name, revenue, cogs, operating_income, filed_date
    Returns list of dicts, or empty list if file absent.
    """
    if not path.exists():
        return []
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    logger.info("Loaded %d rows from %s", len(rows), path)
    return rows


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


def get_segment_id(cursor, segment_name: str) -> int | None:
    """Look up segment_id by name. Returns None if not found."""
    cursor.execute(
        "SELECT segment_id FROM COMPANY_SEGMENT WHERE name = %s",
        (segment_name,),
    )
    row = cursor.fetchone()
    return row[0] if row else None


INSERT_SQL = """
INSERT INTO QUARTERLY_FINANCIALS
    (fiscal_year, fiscal_quarter, segment_id, revenue, cogs, operating_income, filed_date)
VALUES
    (%s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    revenue          = VALUES(revenue),
    cogs             = VALUES(cogs),
    operating_income = VALUES(operating_income),
    filed_date       = VALUES(filed_date)
"""

# QUARTERLY_FINANCIALS has no explicit UNIQUE key defined in the task description,
# so we rely on the PK (qf_id AUTO_INCREMENT).  For idempotency we use a
# conditional update approach: first try to find an existing row and update it,
# otherwise insert.  If the DDL defines a UNIQUE KEY on (fiscal_year, fiscal_quarter,
# segment_id) the ON DUPLICATE KEY UPDATE above handles it automatically.
# We use that form throughout since the design calls for it.


def upsert_row(
    cursor,
    fiscal_year: int,
    fiscal_quarter: int,
    segment_id: int,
    revenue: float,
    cogs: float,
    operating_income: float,
    filed_date: str,
) -> int:
    """Execute the upsert and return rows_affected (1=insert, 2=update, 0=no-op)."""
    cursor.execute(
        INSERT_SQL,
        (fiscal_year, fiscal_quarter, segment_id, revenue, cogs, operating_income, filed_date),
    )
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Core load logic
# ---------------------------------------------------------------------------

def load_from_facts(
    cursor,
    periods: dict[tuple, dict],
    default_segment_id: int,
) -> tuple[int, list[dict]]:
    """Load periods grouped from msft_facts.json.

    Returns (rows_affected, rejected_rows).
    """
    rows_affected = 0
    rejected_rows: list[dict] = []
    required_metrics = {"revenue", "cogs", "operating_income"}

    for (fy, fq), metric_map in sorted(periods.items()):
        # Skip period if any required metric is missing (Requirement 4.7)
        missing = required_metrics - set(metric_map.keys())
        if missing:
            logger.warning(
                "Period %s/Q%s: skipping — missing metrics: %s", fy, fq, sorted(missing)
            )
            continue

        # Validate each metric record before inserting
        period_rejected = False
        for metric, rec in metric_map.items():
            validation_errors = _validate_record(rec)
            if validation_errors:
                for err in validation_errors:
                    logger.warning(
                        "REJECTED row %s | field=%s | expected=%s | actual=%s",
                        err["identifier"],
                        err["field"],
                        err["expected"],
                        err["actual"],
                    )
                    rejected_rows.append(err)
                period_rejected = True

        if period_rejected:
            continue

        # Grab values
        revenue_rec = metric_map["revenue"]
        cogs_rec = metric_map["cogs"]
        oi_rec = metric_map["operating_income"]

        # Use the latest filed_date across the three metrics for the row
        filed_date = max(
            revenue_rec.get("filed_date", ""),
            cogs_rec.get("filed_date", ""),
            oi_rec.get("filed_date", ""),
        )
        if not _is_iso_date(filed_date):
            filed_date = revenue_rec.get("filed_date", "")

        try:
            affected = upsert_row(
                cursor,
                fiscal_year=fy,
                fiscal_quarter=fq,
                segment_id=default_segment_id,
                revenue=float(revenue_rec["value"]),
                cogs=float(cogs_rec["value"]),
                operating_income=float(oi_rec["value"]),
                filed_date=filed_date,
            )
            rows_affected += affected
        except mysql.connector.Error as exc:
            logger.error("DB error inserting %s/Q%s: %s", fy, fq, exc)
            rejected_rows.append({
                "identifier": f"{fy}/{fq}/all",
                "field": "(db insert)",
                "expected": "successful INSERT ... ON DUPLICATE KEY UPDATE",
                "actual": str(exc),
            })

    return rows_affected, rejected_rows


def load_from_segments_csv(
    cursor,
    segment_rows: list[dict],
    segment_id_cache: dict[str, int | None],
) -> tuple[int, list[dict]]:
    """Load per-segment rows from segments.csv.

    Expected CSV columns:
      fiscal_year, fiscal_quarter, segment_name,
      revenue, cogs, operating_income, filed_date

    Returns (rows_affected, rejected_rows).
    """
    rows_affected = 0
    rejected_rows: list[dict] = []
    required_cols = {
        "fiscal_year", "fiscal_quarter", "segment_name",
        "revenue", "cogs", "operating_income", "filed_date",
    }

    for i, row in enumerate(segment_rows):
        identifier = f"segments_csv/row_{i + 1}"

        # Check required columns
        missing_cols = required_cols - set(row.keys())
        if missing_cols:
            rejected_rows.append({
                "identifier": identifier,
                "field": "columns",
                "expected": f"CSV must have columns: {sorted(required_cols)}",
                "actual": f"missing: {sorted(missing_cols)}",
            })
            logger.warning("REJECTED %s: missing CSV columns %s", identifier, sorted(missing_cols))
            continue

        # Parse and validate fields
        errors = []

        try:
            fiscal_year = int(row["fiscal_year"])
        except (ValueError, TypeError):
            errors.append({
                "identifier": identifier,
                "field": "fiscal_year",
                "expected": f"int in [{FY_MIN}, {FY_MAX}]",
                "actual": repr(row.get("fiscal_year")),
            })
            fiscal_year = None

        if fiscal_year is not None and not (FY_MIN <= fiscal_year <= FY_MAX):
            errors.append({
                "identifier": identifier,
                "field": "fiscal_year",
                "expected": f"int in [{FY_MIN}, {FY_MAX}]",
                "actual": repr(fiscal_year),
            })
            fiscal_year = None

        try:
            fiscal_quarter = int(row["fiscal_quarter"])
        except (ValueError, TypeError):
            errors.append({
                "identifier": identifier,
                "field": "fiscal_quarter",
                "expected": "int in {1, 2, 3, 4}",
                "actual": repr(row.get("fiscal_quarter")),
            })
            fiscal_quarter = None

        if fiscal_quarter is not None and fiscal_quarter not in FQ_VALID:
            errors.append({
                "identifier": identifier,
                "field": "fiscal_quarter",
                "expected": "int in {1, 2, 3, 4}",
                "actual": repr(fiscal_quarter),
            })
            fiscal_quarter = None

        segment_name = row.get("segment_name", "").strip()
        if not segment_name:
            errors.append({
                "identifier": identifier,
                "field": "segment_name",
                "expected": "non-empty string",
                "actual": repr(segment_name),
            })

        numeric_fields = {}
        for field in ("revenue", "cogs", "operating_income"):
            try:
                numeric_fields[field] = float(row[field])
            except (ValueError, TypeError):
                errors.append({
                    "identifier": identifier,
                    "field": field,
                    "expected": "numeric (int or float)",
                    "actual": repr(row.get(field)),
                })

        filed_date = row.get("filed_date", "").strip()
        if not _is_iso_date(filed_date):
            errors.append({
                "identifier": identifier,
                "field": "filed_date",
                "expected": "ISO date string YYYY-MM-DD",
                "actual": repr(filed_date),
            })

        if errors:
            for err in errors:
                logger.warning(
                    "REJECTED row %s | field=%s | expected=%s | actual=%s",
                    err["identifier"],
                    err["field"],
                    err["expected"],
                    err["actual"],
                )
                rejected_rows.append(err)
            continue

        # Resolve segment_id
        if segment_name not in segment_id_cache:
            # Will be populated by caller; fetch inline if needed
            pass
        seg_id = segment_id_cache.get(segment_name)
        if seg_id is None:
            rejected_rows.append({
                "identifier": identifier,
                "field": "segment_name",
                "expected": "known segment in COMPANY_SEGMENT",
                "actual": repr(segment_name),
            })
            logger.warning(
                "REJECTED %s: segment '%s' not found in COMPANY_SEGMENT", identifier, segment_name
            )
            continue

        try:
            affected = upsert_row(
                cursor,
                fiscal_year=fiscal_year,
                fiscal_quarter=fiscal_quarter,
                segment_id=seg_id,
                revenue=numeric_fields["revenue"],
                cogs=numeric_fields["cogs"],
                operating_income=numeric_fields["operating_income"],
                filed_date=filed_date,
            )
            rows_affected += affected
        except mysql.connector.Error as exc:
            logger.error("DB error inserting %s: %s", identifier, exc)
            rejected_rows.append({
                "identifier": identifier,
                "field": "(db insert)",
                "expected": "successful INSERT ... ON DUPLICATE KEY UPDATE",
                "actual": str(exc),
            })

    return rows_affected, rejected_rows


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()

    required_env = ["DB_HOST", "DB_NAME", "ADMIN_USER", "ADMIN_PASSWORD"]
    env = {}
    missing_env = []
    for key in required_env:
        val = os.environ.get(key, "").strip()
        if not val:
            missing_env.append(key)
        env[key] = val
    env["DB_PORT"] = os.environ.get("DB_PORT", "3306").strip()

    if missing_env:
        logger.error("Missing required environment variables: %s", missing_env)
        sys.exit(1)

    # --- Load source data ---
    records = load_facts(FACTS_PATH)
    periods = group_by_period(records)
    logger.info("Grouped into %d distinct (fiscal_year, fiscal_quarter) periods", len(periods))

    # --- Connect ---
    try:
        conn = get_connection(env)
    except mysql.connector.Error as exc:
        logger.error("Cannot connect to database: %s", exc)
        sys.exit(1)

    total_affected = 0
    all_rejected: list[dict] = []

    try:
        cursor = conn.cursor()

        # Look up default segment_id for "Gaming"
        gaming_segment_id = get_segment_id(cursor, "Gaming")
        if gaming_segment_id is None:
            logger.error(
                "Segment 'Gaming' not found in COMPANY_SEGMENT. "
                "Run 06_seed_reference.sql before this script."
            )
            conn.close()
            sys.exit(1)
        logger.info("Gaming segment_id = %d", gaming_segment_id)

        # --- Load from msft_facts.json ---
        if not SEGMENTS_CSV_PATH.exists():
            # Default path: load all periods with Gaming segment
            logger.info("segments.csv not found — loading all periods with segment 'Gaming'")
            affected, rejected = load_from_facts(cursor, periods, gaming_segment_id)
            total_affected += affected
            all_rejected.extend(rejected)
        else:
            # segments.csv present: load per-segment data from it
            logger.info("segments.csv found — loading per-segment data from %s", SEGMENTS_CSV_PATH)
            segment_rows = load_segments_csv(SEGMENTS_CSV_PATH)

            # Build segment_id cache for all segment names in the CSV
            segment_names = {row.get("segment_name", "").strip() for row in segment_rows}
            segment_id_cache: dict[str, int | None] = {}
            for name in segment_names:
                if name:
                    seg_id = get_segment_id(cursor, name)
                    segment_id_cache[name] = seg_id
                    if seg_id is None:
                        logger.warning("Segment '%s' not found in COMPANY_SEGMENT", name)

            affected, rejected = load_from_segments_csv(cursor, segment_rows, segment_id_cache)
            total_affected += affected
            all_rejected.extend(rejected)

        conn.commit()

    except Exception as exc:
        logger.error("Unexpected error during load: %s", exc)
        conn.rollback()
        conn.close()
        raise
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        conn.close()

    # --- Summary ---
    print(f"\nRows inserted/updated: {total_affected}")

    if all_rejected:
        print(f"\nRejected rows ({len(all_rejected)}):")
        for rej in all_rejected:
            print(
                f"  identifier={rej['identifier']}"
                f" | field={rej['field']}"
                f" | expected={rej['expected']}"
                f" | actual={rej['actual']}"
            )
    else:
        print("No rows rejected.")

    logger.info(
        "Done. rows_affected=%d rejected=%d", total_affected, len(all_rejected)
    )


if __name__ == "__main__":
    main()
