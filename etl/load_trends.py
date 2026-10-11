"""
load_trends.py — Fetch Google Trends data and load into GOOGLE_TREND table.

Keywords fetched: "Call of Duty", "Game Pass", "Xbox price"

Flow:
  1. Try pytrends for each keyword (5-year timeframe, daily interest scores).
  2. On TooManyRequestsError: fall back to data/raw/trends_manual.csv and log
     the rate-limit event with a UTC timestamp.
  3. If neither source is available: log error and exit 1.
  4. Write each keyword's results to data/raw/trends_<keyword_slug>.csv
  5. INSERT IGNORE rows into GOOGLE_TREND (idempotency).

Manual CSV fallback format (data/raw/trends_manual.csv):
  Columns: keyword, obs_date, interest_score
    keyword        VARCHAR(200) — must match one of the target keywords exactly
    obs_date       DATE in ISO 8601 format (YYYY-MM-DD)
    interest_score TINYINT UNSIGNED (0-100, Google Trends normalized scale)

Requirements: 4.3, 4.10
"""

import csv
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import mysql.connector
from dotenv import load_dotenv
from pytrends.exceptions import TooManyRequestsError
from pytrends.request import TrendReq

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KEYWORDS = ["Call of Duty", "Game Pass", "Xbox price"]

# pytrends timeframe: approximately 5 years of weekly data
TIMEFRAME = "today 5-y"
GEO = ""  # worldwide

# Seconds to wait between keyword requests (politeness delay)
REQUEST_DELAY_SECONDS = 2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw"
MANUAL_CSV_PATH = DATA_RAW / "trends_manual.csv"

# ---------------------------------------------------------------------------
# Logging — UTC timestamps
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logging.Formatter.converter = lambda *args: datetime.now(timezone.utc).timetuple()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def keyword_slug(keyword: str) -> str:
    """Convert keyword to a filesystem-safe slug for output filenames.

    E.g. "Call of Duty" → "call_of_duty"
    """
    slug = keyword.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")


def utc_now_iso() -> str:
    """Return current UTC datetime as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# pytrends fetch
# ---------------------------------------------------------------------------

def fetch_via_pytrends(keyword: str) -> list[dict] | None:
    """Fetch Google Trends interest-over-time for a single keyword.

    Returns a list of {"keyword": ..., "obs_date": "YYYY-MM-DD", "interest_score": int}
    dicts, or None if the fetch fails for a non-rate-limit reason.

    Raises TooManyRequestsError if Google rate-limits the request (caller
    is responsible for catching and triggering the fallback).
    """
    pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
    pytrends.build_payload([keyword], timeframe=TIMEFRAME, geo=GEO)
    df = pytrends.interest_over_time()

    if df is None or df.empty:
        logger.warning("pytrends returned empty DataFrame for keyword '%s'", keyword)
        return []

    # Remove the "isPartial" column if present; it marks the current week
    if "isPartial" in df.columns:
        df = df.drop(columns=["isPartial"])

    rows = []
    for date_idx, row in df.iterrows():
        # date_idx is a pandas Timestamp; convert to ISO date string
        obs_date = date_idx.strftime("%Y-%m-%d")
        # interest_score is the value in the column matching the keyword;
        # Google Trends returns 0-100 (TINYINT UNSIGNED is safe)
        score = int(row.get(keyword, 0))
        rows.append({"keyword": keyword, "obs_date": obs_date, "interest_score": score})

    logger.info("pytrends: fetched %d rows for keyword '%s'", len(rows), keyword)
    return rows


# ---------------------------------------------------------------------------
# Manual CSV fallback
# ---------------------------------------------------------------------------

def load_manual_csv(path: Path) -> dict[str, list[dict]]:
    """Read trends_manual.csv and return a dict keyed by keyword.

    Each value is a list of {"keyword": ..., "obs_date": ..., "interest_score": int}.

    Skips rows with invalid or missing fields and logs them.

    Fallback CSV format (columns required):
      keyword, obs_date, interest_score
    """
    if not path.exists():
        return {}

    result: dict[str, list[dict]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for lineno, row in enumerate(reader, start=2):  # 1-indexed; row 1 = header
            keyword = row.get("keyword", "").strip()
            obs_date_raw = row.get("obs_date", "").strip()
            score_raw = row.get("interest_score", "").strip()

            # Validate keyword
            if not keyword:
                logger.warning(
                    "trends_manual.csv line %d: missing keyword — skipping", lineno
                )
                continue

            # Validate obs_date (ISO 8601 YYYY-MM-DD)
            try:
                datetime.strptime(obs_date_raw, "%Y-%m-%d")
            except ValueError:
                logger.warning(
                    "trends_manual.csv line %d (keyword='%s'): invalid obs_date '%s' — skipping",
                    lineno, keyword, obs_date_raw,
                )
                continue

            # Validate interest_score (0–100 integer)
            try:
                score = int(score_raw)
                if not (0 <= score <= 100):
                    raise ValueError("out of range")
            except (ValueError, TypeError):
                logger.warning(
                    "trends_manual.csv line %d (keyword='%s'): invalid interest_score '%s' "
                    "(expected int 0-100) — skipping",
                    lineno, keyword, score_raw,
                )
                continue

            result.setdefault(keyword, []).append(
                {"keyword": keyword, "obs_date": obs_date_raw, "interest_score": score}
            )

    total = sum(len(v) for v in result.values())
    logger.info("Loaded %d rows from manual CSV %s (keywords: %s)", total, path, list(result.keys()))
    return result


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

def write_keyword_csv(rows: list[dict], keyword: str, output_dir: Path) -> Path:
    """Write rows for a single keyword to data/raw/trends_<slug>.csv."""
    slug = keyword_slug(keyword)
    output_path = output_dir / f"trends_{slug}.csv"
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["keyword", "obs_date", "interest_score"])
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Wrote %d rows to %s", len(rows), output_path)
    return output_path


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


INSERT_SQL = """
INSERT IGNORE INTO GOOGLE_TREND (keyword, obs_date, interest_score)
VALUES (%s, %s, %s)
"""


def db_insert_rows(conn: mysql.connector.MySQLConnection, rows: list[dict]) -> int:
    """Bulk-insert rows into GOOGLE_TREND with INSERT IGNORE for idempotency.

    Returns the number of rows actually inserted.
    """
    if not rows:
        return 0
    cursor = conn.cursor()
    data = [(r["keyword"], r["obs_date"], r["interest_score"]) for r in rows]
    cursor.executemany(INSERT_SQL, data)
    affected = cursor.rowcount
    conn.commit()
    cursor.close()
    return affected


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()

    required_env = ["DB_HOST", "DB_NAME", "ADMIN_USER", "ADMIN_PASSWORD"]
    env: dict[str, str] = {}
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

    # Ensure output directory exists
    DATA_RAW.mkdir(parents=True, exist_ok=True)

    # Collect results per keyword: keyword → list of row dicts
    results: dict[str, list[dict]] = {}
    rate_limited = False

    for i, keyword in enumerate(KEYWORDS):
        if i > 0 and not rate_limited:
            # Politeness delay between requests when using the live API
            time.sleep(REQUEST_DELAY_SECONDS)

        if rate_limited:
            # Already fell back; skip live fetch for remaining keywords
            logger.info(
                "Rate-limit fallback active — skipping pytrends for keyword '%s'", keyword
            )
            continue

        try:
            rows = fetch_via_pytrends(keyword)
            if rows is not None:
                results[keyword] = rows
        except TooManyRequestsError:
            rate_limited = True
            logger.warning(
                "RATE_LIMIT event at %s — pytrends returned TooManyRequestsError "
                "while fetching keyword '%s'; switching to manual CSV fallback.",
                utc_now_iso(),
                keyword,
            )

    # Apply manual CSV fallback for any keyword not yet fetched (either because
    # rate-limited or pytrends returned None)
    keywords_needing_fallback = [kw for kw in KEYWORDS if kw not in results]
    if keywords_needing_fallback:
        logger.info(
            "Falling back to manual CSV for keywords: %s", keywords_needing_fallback
        )
        manual_data = load_manual_csv(MANUAL_CSV_PATH)

        for keyword in keywords_needing_fallback:
            if keyword in manual_data:
                results[keyword] = manual_data[keyword]
                logger.info(
                    "Manual CSV fallback: loaded %d rows for keyword '%s'",
                    len(manual_data[keyword]),
                    keyword,
                )
            else:
                logger.error(
                    "No data available for keyword '%s': pytrends failed or was rate-limited "
                    "and keyword not found in manual CSV (%s).",
                    keyword,
                    MANUAL_CSV_PATH,
                )

    # Check if any keyword ended up with no data at all
    missing_keywords = [kw for kw in KEYWORDS if kw not in results or not results[kw]]
    if missing_keywords:
        logger.error(
            "Neither pytrends nor the manual CSV provided data for keywords: %s. "
            "Create data/raw/trends_manual.csv with the required keywords before re-running.",
            missing_keywords,
        )
        sys.exit(1)

    # Write per-keyword CSV output files
    for keyword, rows in results.items():
        write_keyword_csv(rows, keyword, DATA_RAW)

    # --- Database load ---
    try:
        conn = get_connection(env)
    except mysql.connector.Error as exc:
        logger.error("Cannot connect to database: %s", exc)
        sys.exit(1)

    total_inserted = 0
    try:
        all_rows = [row for rows_list in results.values() for row in rows_list]
        total_inserted = db_insert_rows(conn, all_rows)
    except mysql.connector.Error as exc:
        logger.error("Database error during GOOGLE_TREND insert: %s", exc)
        conn.close()
        sys.exit(1)
    finally:
        conn.close()

    total_rows = sum(len(v) for v in results.values())
    logger.info(
        "Done. keywords=%d total_rows=%d db_inserted=%d",
        len(results),
        total_rows,
        total_inserted,
    )
    print(
        f"\nGoogle Trends load complete: {len(results)} keyword(s), "
        f"{total_rows} total rows, {total_inserted} new rows inserted into GOOGLE_TREND."
    )


if __name__ == "__main__":
    main()
