"""
load_financials.py — Load PBP/IC/MPC quarterly financials into QUARTERLY_FINANCIALS.

Sources:
  data/raw/segments_reported.csv  — PBP/IC/MPC segment revenue + operating income
                                    transcribed from Microsoft earnings releases
  data/raw/msft_facts.json        — EDGAR company-wide totals (validation ONLY;
                                    never inserted as segment rows)

Target:  QUARTERLY_FINANCIALS table in margin_db

Columns in segments_reported.csv:
  fiscal_year, fiscal_quarter, segment_name, revenue_m, operating_income_m,
  source_url, basis

  basis values:
    'as_reported_pre_recast' — filed before the August 2024 segment recast
    'recast_2024-08'         — restated on the August 2024 recast structure
    'as_reported'            — post-recast filings going forward

  revenue_m / operating_income_m are already in millions USD (insert as-is).
  COGS is not reported at segment level; the cogs column is left NULL.
  basis and source_url are stored in QUARTERLY_FINANCIALS (added by migration 08).

  IMPORTANT: FY23 rows (as_reported_pre_recast) and FY24 rows (recast_2024-08)
  are not directly comparable. Any query computing period-over-period changes
  must filter on basis or acknowledge the comparability caveat.

Idempotency: INSERT ... ON DUPLICATE KEY UPDATE, keyed on
  UNIQUE KEY uq_qf_period (fiscal_year, fiscal_quarter, segment_id)
  added by migration 08 section 10c.

Post-load validator (Requirement 4.1):
  For each (fiscal_year, fiscal_quarter) that has all three segments loaded,
  abs(PBP + IC + MPC - EDGAR_company_wide) / EDGAR_company_wide must be <= 0.5%.
  Quarters that fail are logged as warnings; script does NOT abort (earnings
  releases round to millions, so small gaps are expected).

Field-level validation: rejected rows logged with identifier, field, expected,
actual. Per-row rejection; does not batch-abort.

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
SEGMENTS_REPORTED_PATH = PROJECT_ROOT / "data" / "raw" / "segments_reported.csv"
FACTS_PATH             = PROJECT_ROOT / "data" / "raw" / "msft_facts.json"

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

VALID_SEGMENT_NAMES = frozenset({
    "Productivity and Business Processes",
    "Intelligent Cloud",
    "More Personal Computing",
})

# Three valid basis values matching MARGIN_DB.md §8.3 and §12.1
VALID_BASIS_VALUES = frozenset({
    "as_reported_pre_recast",  # FY23 filings, filed before August 2024 segment recast
    "recast_2024-08",          # FY24 rows restated on the August 2024 recast structure
    "as_reported",             # post-recast filings going forward
})

FY_MIN, FY_MAX = 2000, 2100
FQ_VALID = {1, 2, 3, 4}

# Tolerance for abs(PBP+IC+MPC - EDGAR_total) / EDGAR_total (0.5%)
EDGAR_TOLERANCE = 0.005


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _is_iso_date(value: str) -> bool:
    if not value or len(value) != 10:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def validate_segment_row(row_num: int, row: dict) -> tuple[dict | None, list[dict]]:
    """Validate one row from segments_reported.csv.

    Returns (validated_dict, rejection_list).
    validated_dict is None if any required field fails.
    """
    rejections: list[dict] = []
    identifier = f"segments_reported/row_{row_num}"

    # fiscal_year
    try:
        fiscal_year = int(row["fiscal_year"])
        if not (FY_MIN <= fiscal_year <= FY_MAX):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        rejections.append({
            "identifier": identifier,
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
            "identifier": identifier,
            "field": "fiscal_quarter",
            "expected": "int in {1, 2, 3, 4}",
            "actual": repr(row.get("fiscal_quarter")),
        })
        fiscal_quarter = None

    # segment_name — must be one of PBP, IC, MPC
    segment_name = row.get("segment_name", "").strip()
    if segment_name not in VALID_SEGMENT_NAMES:
        rejections.append({
            "identifier": identifier,
            "field": "segment_name",
            "expected": f"one of {sorted(VALID_SEGMENT_NAMES)}",
            "actual": repr(segment_name),
        })
        segment_name = None

    # revenue_m — required, non-negative
    try:
        revenue_m = float(row["revenue_m"])
        if revenue_m < 0:
            raise ValueError("negative")
    except (ValueError, KeyError, TypeError):
        rejections.append({
            "identifier": identifier,
            "field": "revenue_m",
            "expected": "non-negative float (millions USD)",
            "actual": repr(row.get("revenue_m")),
        })
        revenue_m = None

    # operating_income_m — optional (blank → None)
    oi_raw = row.get("operating_income_m", "").strip()
    if oi_raw == "":
        operating_income_m = None
    else:
        try:
            operating_income_m = float(oi_raw)
        except (ValueError, TypeError):
            rejections.append({
                "identifier": identifier,
                "field": "operating_income_m",
                "expected": "float (millions USD) or blank for NULL",
                "actual": repr(oi_raw),
            })
            operating_income_m = None

    # source_url — required, non-empty
    source_url = row.get("source_url", "").strip()
    if not source_url:
        rejections.append({
            "identifier": identifier,
            "field": "source_url",
            "expected": "non-empty URL string",
            "actual": repr(row.get("source_url")),
        })

    # basis — required, one of the three known values
    basis = row.get("basis", "").strip()
    if basis not in VALID_BASIS_VALUES:
        rejections.append({
            "identifier": identifier,
            "field": "basis",
            "expected": f"one of {sorted(VALID_BASIS_VALUES)}",
            "actual": repr(basis),
        })

    if rejections:
        return None, rejections

    return {
        "fiscal_year":        fiscal_year,
        "fiscal_quarter":     fiscal_quarter,
        "segment_name":       segment_name,
        "revenue_m":          revenue_m,
        "operating_income_m": operating_income_m,
        "source_url":         source_url,
        "basis":              basis,
    }, []


# ---------------------------------------------------------------------------
# EDGAR company-wide totals (validator only — never inserted as segment rows)
# ---------------------------------------------------------------------------

def load_edgar_company_totals(facts_path: Path) -> dict[tuple[int, int], float]:
    """Read msft_facts.json and return company-wide revenue in millions USD.

    Returns {(fiscal_year, fiscal_quarter): revenue_millions}.
    Only quarterly frames included; latest filed_date per period for restatements.
    Returns empty dict if file is absent (validator skipped with a warning).
    """
    if not facts_path.exists():
        logger.warning(
            "msft_facts.json not found at %s — EDGAR validator will be skipped.",
            facts_path,
        )
        return {}

    with open(facts_path, encoding="utf-8") as f:
        records = json.load(f)

    if not isinstance(records, list):
        logger.warning("msft_facts.json is not a JSON array — EDGAR validator skipped.")
        return {}

    # Keep only 'revenue' metric, quarterly frames, latest filed_date per period
    best: dict[tuple[int, int], dict] = {}
    for rec in records:
        if rec.get("metric") != "revenue":
            continue
        fy = rec.get("fiscal_year")
        fq = rec.get("fiscal_quarter")
        if not (isinstance(fy, int) and isinstance(fq, int) and fq in FQ_VALID):
            continue
        key = (fy, fq)
        filed = rec.get("filed_date", "")
        existing = best.get(key)
        if existing is None or filed > existing.get("filed_date", ""):
            best[key] = rec

    totals: dict[tuple[int, int], float] = {}
    for (fy, fq), rec in best.items():
        raw_value = rec.get("value")
        if isinstance(raw_value, (int, float)):
            # EDGAR values are in raw USD; divide by 1,000,000 for millions
            totals[(fy, fq)] = float(raw_value) / 1_000_000

    logger.info(
        "EDGAR company-wide revenue: loaded %d quarterly periods for validation.",
        len(totals),
    )
    return totals


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


def get_segment_id(cursor, segment_name: str) -> int | None:
    cursor.execute(
        "SELECT segment_id FROM COMPANY_SEGMENT WHERE name = %s", (segment_name,)
    )
    row = cursor.fetchone()
    return row[0] if row else None


# cogs stays NULL — not reported at segment level in segments_reported.csv.
# filed_date stays NULL — source_url records the earnings release instead.
# ON DUPLICATE KEY UPDATE is effective because migration 08 section 10c adds
# UNIQUE KEY uq_qf_period (fiscal_year, fiscal_quarter, segment_id).
INSERT_SQL = """
INSERT INTO QUARTERLY_FINANCIALS
    (fiscal_year, fiscal_quarter, segment_id,
     revenue, cogs, operating_income,
     basis, source_url)
VALUES
    (%s, %s, %s,
     %s, NULL, %s,
     %s, %s)
ON DUPLICATE KEY UPDATE
    revenue          = VALUES(revenue),
    operating_income = VALUES(operating_income),
    basis            = VALUES(basis),
    source_url       = VALUES(source_url)
"""


def upsert_row(
    cursor,
    fiscal_year: int,
    fiscal_quarter: int,
    segment_id: int,
    revenue_m: float,
    operating_income_m: float | None,
    basis: str,
    source_url: str,
) -> int:
    cursor.execute(
        INSERT_SQL,
        (
            fiscal_year, fiscal_quarter, segment_id,
            revenue_m, operating_income_m,
            basis, source_url,
        ),
    )
    return cursor.rowcount


# ---------------------------------------------------------------------------
# Post-load EDGAR validator
# ---------------------------------------------------------------------------

def run_edgar_validator(
    cursor,
    edgar_totals: dict[tuple[int, int], float],
) -> None:
    """Compare PBP+IC+MPC sum against EDGAR company-wide revenue per quarter.

    Uses abs(sum - EDGAR_total) / EDGAR_total — symmetric, catches both
    under-count and over-count. Logs a WARNING for any quarter exceeding
    EDGAR_TOLERANCE (0.5%). Does NOT abort — earnings releases round to
    millions, so small gaps are expected.

    Reports three counts:
      passed  — abs_delta <= 0.5%
      warned  — abs_delta >  0.5%
      skipped — no EDGAR figure available for that (fiscal_year, fiscal_quarter)

    Logs ERROR (not just info) if zero quarters were compared. This catches
    the case where msft_facts.json covers a different year range than the
    data in QUARTERLY_FINANCIALS (e.g. stale or incomplete fetch).
    """
    if not edgar_totals:
        logger.info("EDGAR validator skipped (no msft_facts.json data available).")
        return

    # Sum what was just loaded into QUARTERLY_FINANCIALS, per (fy, fq)
    cursor.execute(
        """
        SELECT fiscal_year, fiscal_quarter, SUM(revenue) AS total_rev
        FROM QUARTERLY_FINANCIALS
        GROUP BY fiscal_year, fiscal_quarter
        """
    )
    db_totals: dict[tuple[int, int], float] = {
        (int(fy), int(fq)): float(rev)
        for fy, fq, rev in cursor.fetchall()
    }

    passed = 0
    warned = 0
    skipped = 0
    for (fy, fq), edgar_rev in sorted(edgar_totals.items()):
        db_rev = db_totals.get((fy, fq))
        if db_rev is None:
            # No matching period in QUARTERLY_FINANCIALS for this EDGAR entry
            skipped += 1
            continue
        if edgar_rev == 0:
            skipped += 1
            continue
        # Symmetric: catches both under-count and over-count
        delta_pct = abs(db_rev - edgar_rev) / edgar_rev
        if delta_pct > EDGAR_TOLERANCE:
            logger.warning(
                "EDGAR validator WARN FY%dQ%d: "
                "PBP+IC+MPC sum=%.2f M  EDGAR total=%.2f M  "
                "abs_delta=%.3f%%  (threshold %.1f%%)",
                fy, fq, db_rev, edgar_rev,
                delta_pct * 100, EDGAR_TOLERANCE * 100,
            )
            warned += 1
        else:
            logger.info(
                "EDGAR validator PASS FY%dQ%d: "
                "PBP+IC+MPC sum=%.2f M  EDGAR total=%.2f M  abs_delta=%.3f%%",
                fy, fq, db_rev, edgar_rev, delta_pct * 100,
            )
            passed += 1

    logger.info(
        "EDGAR validator complete: %d passed, %d warned (>%.1f%%), %d skipped "
        "(no EDGAR figure for that period).",
        passed, warned, EDGAR_TOLERANCE * 100, skipped,
    )
    if passed == 0 and warned == 0:
        logger.error(
            "EDGAR validator matched ZERO quarters. "
            "msft_facts.json covers FY%s-FY%s; QUARTERLY_FINANCIALS covers FY%s-FY%s. "
            "Re-run fetch_edgar.py to refresh msft_facts.json before trusting "
            "the loaded segment data.",
            min(k[0] for k in edgar_totals),
            max(k[0] for k in edgar_totals),
            min(k[0] for k in db_totals) if db_totals else "?",
            max(k[0] for k in db_totals) if db_totals else "?",
        )


# ---------------------------------------------------------------------------
# Core load logic
# ---------------------------------------------------------------------------

def load_from_segments_reported(
    cursor,
    csv_path: Path,
    segment_id_cache: dict[str, int | None],
) -> tuple[int, list[dict]]:
    """Read segments_reported.csv and upsert PBP/IC/MPC rows.

    Returns (rows_affected, rejected_rows).
    """
    rows_affected = 0
    rejected_rows: list[dict] = []

    required_cols = {
        "fiscal_year", "fiscal_quarter", "segment_name",
        "revenue_m", "operating_income_m", "source_url", "basis",
    }

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            logger.error("segments_reported.csv appears empty or has no header.")
            sys.exit(1)
        # Strip BOM / surrounding whitespace from header names
        reader.fieldnames = [h.strip().lstrip("\ufeff") for h in reader.fieldnames]
        missing_cols = required_cols - set(reader.fieldnames)
        if missing_cols:
            logger.error(
                "segments_reported.csv missing required columns: %s (found: %s)",
                sorted(missing_cols),
                reader.fieldnames,
            )
            sys.exit(1)

        for row_num, row in enumerate(reader, start=2):
            validated, rejections = validate_segment_row(row_num, row)

            if rejections:
                for err in rejections:
                    logger.warning(
                        "REJECTED row %s | field=%s | expected=%s | actual=%s",
                        err["identifier"], err["field"],
                        err["expected"], err["actual"],
                    )
                    rejected_rows.append(err)
                continue

            seg_name = validated["segment_name"]
            seg_id = segment_id_cache.get(seg_name)
            if seg_id is None:
                logger.warning(
                    "REJECTED row %d: segment '%s' not found in COMPANY_SEGMENT — "
                    "run 06_seed_reference.sql or 09_fix_segments.sql first.",
                    row_num, seg_name,
                )
                rejected_rows.append({
                    "identifier": f"segments_reported/row_{row_num}",
                    "field": "segment_name",
                    "expected": "known segment in COMPANY_SEGMENT",
                    "actual": repr(seg_name),
                })
                continue

            logger.debug(
                "Loading FY%dQ%d  %-42s  revenue_m=%.2f  basis=%s",
                validated["fiscal_year"], validated["fiscal_quarter"],
                seg_name, validated["revenue_m"], validated["basis"],
            )

            try:
                affected = upsert_row(
                    cursor,
                    validated["fiscal_year"],
                    validated["fiscal_quarter"],
                    seg_id,
                    validated["revenue_m"],
                    validated["operating_income_m"],
                    validated["basis"],
                    validated["source_url"],
                )
                rows_affected += affected
            except mysql.connector.Error as exc:
                logger.error(
                    "DB error inserting row %d (%s FY%dQ%d): %s",
                    row_num, seg_name,
                    validated["fiscal_year"], validated["fiscal_quarter"], exc,
                )
                rejected_rows.append({
                    "identifier": f"segments_reported/row_{row_num}",
                    "field": "(db insert)",
                    "expected": "successful upsert",
                    "actual": str(exc),
                })

    return rows_affected, rejected_rows


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()

    required_env = ["DB_HOST", "DB_NAME", "ADMIN_USER", "ADMIN_PASSWORD"]
    env: dict = {}
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

    if not SEGMENTS_REPORTED_PATH.exists():
        logger.error(
            "segments_reported.csv not found at %s. "
            "Please transcribe PBP/IC/MPC data from Microsoft earnings releases "
            "(10-Q/10-K Segment Results section).",
            SEGMENTS_REPORTED_PATH,
        )
        sys.exit(1)

    # Load EDGAR totals for post-load validation (missing file → validator skipped)
    edgar_totals = load_edgar_company_totals(FACTS_PATH)

    try:
        conn = get_connection(env)
    except mysql.connector.Error as exc:
        logger.error("Cannot connect to database: %s", exc)
        sys.exit(1)

    total_affected = 0
    all_rejected: list[dict] = []

    try:
        cursor = conn.cursor()

        # Build segment_id cache for PBP, IC, MPC only — no Gaming lookup
        segment_id_cache: dict[str, int | None] = {}
        for seg_name in VALID_SEGMENT_NAMES:
            seg_id = get_segment_id(cursor, seg_name)
            segment_id_cache[seg_name] = seg_id
            if seg_id is None:
                logger.warning(
                    "Segment '%s' not found in COMPANY_SEGMENT — rows for this "
                    "segment will be rejected. Run 06_seed_reference.sql (or "
                    "09_fix_segments.sql for older schemas) first.",
                    seg_name,
                )

        logger.info("Loading %s ...", SEGMENTS_REPORTED_PATH)
        affected, rejected = load_from_segments_reported(
            cursor, SEGMENTS_REPORTED_PATH, segment_id_cache
        )
        total_affected += affected
        all_rejected.extend(rejected)

        conn.commit()

        # Post-load EDGAR validator (symmetric abs-delta check)
        run_edgar_validator(cursor, edgar_totals)

    except Exception as exc:
        logger.error("Unexpected error: %s", exc, exc_info=True)
        conn.rollback()
        conn.close()
        raise
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        conn.close()

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
