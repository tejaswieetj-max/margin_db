"""
fetch_edgar.py — Fetch Microsoft quarterly financial facts from SEC EDGAR.

Source: https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json
Writes:  data/raw/msft_facts.json

Metrics extracted:
  - Revenue:          us-gaap.Revenues
  - COGS:             us-gaap.CostOfRevenue
  - Operating Income: us-gaap.OperatingIncomeLoss

Q4 derivation method (documented in docs/MARGIN_DB.md):
  Microsoft does not always file an explicit Q4 10-Q because the 10-K covers
  the full fiscal year.  We derive Q4 as:
      Q4 = FY_annual_value − (Q1 + Q2 + Q3)
  where FY_annual_value is the value associated with fp="FY" (or form="10-K")
  for the same fiscal year and metric.  If the FY annual value is not
  available for a given metric we cannot derive Q4 and we log the gap.

Requirements: 4.1, 4.10, 21.1
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

EDGAR_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json"

# Map friendly metric names → EDGAR us-gaap concept names
METRIC_MAP = {
    "revenue": "Revenues",
    "cogs": "CostOfRevenue",
    "operating_income": "OperatingIncomeLoss",
}

# Paths are relative to the project root (one level above etl/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "data" / "raw" / "msft_facts.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
# Force UTC timestamps in log records
logging.Formatter.converter = lambda *args: datetime.now(timezone.utc).timetuple()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_company_facts(url: str, user_agent: str) -> dict:
    """GET the SEC EDGAR company-facts JSON.

    Non-200 responses are logged (with UTC timestamp + URL) and cause exit(1).
    """
    logger.info("Fetching %s", url)
    try:
        response = requests.get(url, headers={"User-Agent": user_agent}, timeout=30)
    except requests.RequestException as exc:
        utc_now = datetime.now(timezone.utc).isoformat()
        logger.error("%s | %s | network error: %s", utc_now, url, exc)
        sys.exit(1)

    if response.status_code != 200:
        utc_now = datetime.now(timezone.utc).isoformat()
        logger.error(
            "%s | %s | HTTP %s", utc_now, url, response.status_code
        )
        sys.exit(1)

    return response.json()


# ---------------------------------------------------------------------------
# Extract quarterly frames from a single EDGAR concept
# ---------------------------------------------------------------------------

def extract_usd_units(concept_data: dict) -> list[dict]:
    """Return the list of USD-unit fact entries for a concept, or []."""
    return concept_data.get("units", {}).get("USD", [])


def is_quarterly_frame(entry: dict) -> bool:
    """True if this entry represents a single fiscal quarter (not annual/TTM).

    We keep:
      fp in {Q1, Q2, Q3, Q4}  — explicitly filed quarterly frame
      fp == FY                 — annual frame (used for Q4 derivation only)
      form == 10-K             — annual filing (used for Q4 derivation only)

    We exclude:
      CY* frames (calendar-year TTM entries from the frame field)
      fp == H1 / H2 / 9M      — semi-annual or 9-month frames
    """
    fp = entry.get("fp", "")
    form = entry.get("form", "")
    return fp in {"Q1", "Q2", "Q3", "Q4", "FY"} or form == "10-K"


# ---------------------------------------------------------------------------
# Deduplication: keep latest filed_date per (fy, fp/form, metric)
# ---------------------------------------------------------------------------

def deduplicate(entries: list[dict]) -> list[dict]:
    """Keep the entry with the latest 'filed' date per (fy, fp) key.

    Requirement 4.7 / 4.10: deduplicate restatements by keeping latest filed_date.
    """
    best: dict[tuple, dict] = {}
    for entry in entries:
        fy = entry.get("fy")
        fp = entry.get("fp", "")
        form = entry.get("form", "")
        # Normalise annual marker
        fp_key = "FY" if (fp == "FY" or form == "10-K") else fp
        key = (fy, fp_key)
        filed = entry.get("filed", "")
        existing = best.get(key)
        if existing is None or filed > existing.get("filed", ""):
            best[key] = entry
    return list(best.values())


# ---------------------------------------------------------------------------
# Build a per-(fy, quarter) lookup from deduplicated entries
# ---------------------------------------------------------------------------

def build_quarterly_lookup(
    entries: list[dict],
) -> tuple[dict[tuple[int, int], dict], dict[int, dict]]:
    """Return (quarterly_map, annual_map).

    quarterly_map: {(fy, quarter_int): entry}  — fp Q1/Q2/Q3/Q4 entries
    annual_map:    {fy: entry}                  — fp FY / 10-K entries
    """
    quarterly: dict[tuple[int, int], dict] = {}
    annual: dict[int, dict] = {}

    for entry in entries:
        fy = entry.get("fy")
        fp = entry.get("fp", "")
        form = entry.get("form", "")

        if fy is None:
            continue

        # Annual entry
        if fp == "FY" or form == "10-K":
            annual[fy] = entry
            continue

        # Explicit quarterly entry
        fp_to_q = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
        q = fp_to_q.get(fp)
        if q is not None:
            quarterly[(fy, q)] = entry

    return quarterly, annual


# ---------------------------------------------------------------------------
# Q4 derivation
# ---------------------------------------------------------------------------

def derive_q4(
    metric: str,
    quarterly: dict[tuple[int, int], dict],
    annual: dict[int, dict],
    missing_quarters: list[str],
) -> dict[tuple[int, int], dict]:
    """Derive Q4 entries where not already present.

    Q4 = FY_annual - (Q1 + Q2 + Q3)

    If the FY annual is not available, or any of Q1/Q2/Q3 is missing, we log
    the gap and add a note to missing_quarters.

    Returns an updated quarterly map that includes derived Q4 entries.
    """
    result = dict(quarterly)  # shallow copy

    # Collect all fiscal years we have any data for
    all_fys: set[int] = set()
    for (fy, _q) in quarterly:
        all_fys.add(fy)
    all_fys.update(annual.keys())

    for fy in sorted(all_fys):
        # Skip if Q4 is already explicitly present
        if (fy, 4) in result:
            continue

        # Need the annual value
        ann = annual.get(fy)
        if ann is None:
            msg = f"metric={metric} fy={fy} Q4: FY annual not available — Q4 skipped"
            logger.warning(msg)
            missing_quarters.append(msg)
            print(f"MISSING QUARTER: {msg}")
            continue

        # Need Q1, Q2, Q3
        q1 = quarterly.get((fy, 1))
        q2 = quarterly.get((fy, 2))
        q3 = quarterly.get((fy, 3))

        missing = [f"Q{i}" for i, q in [(1, q1), (2, q2), (3, q3)] if q is None]
        if missing:
            msg = (
                f"metric={metric} fy={fy} Q4: "
                f"missing {', '.join(missing)} — cannot derive Q4"
            )
            logger.warning(msg)
            missing_quarters.append(msg)
            print(f"MISSING QUARTER: {msg}")
            continue

        fy_val = ann["val"]
        derived_val = fy_val - (q1["val"] + q2["val"] + q3["val"])

        # Use the annual entry's filed date as the filed_date for derived Q4
        result[(fy, 4)] = {
            "fy": fy,
            "fp": "Q4",
            "val": derived_val,
            "filed": ann.get("filed", ""),
            "end": ann.get("end", ""),
            "derived": True,
        }
        logger.debug(
            "Derived Q4 for metric=%s fy=%d: %s - (%s+%s+%s) = %s",
            metric,
            fy,
            fy_val,
            q1["val"],
            q2["val"],
            q3["val"],
            derived_val,
        )

    return result


# ---------------------------------------------------------------------------
# Main extraction logic
# ---------------------------------------------------------------------------

def extract_facts(company_facts: dict) -> list[dict]:
    """Extract quarterly financial facts and return output records.

    Returns a list of dicts matching the output schema:
      {fiscal_year, fiscal_quarter, metric, value, filed_date}
    """
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})
    missing_quarters: list[str] = []
    output: list[dict] = []

    for metric_name, concept_name in METRIC_MAP.items():
        concept = us_gaap.get(concept_name)
        if concept is None:
            logger.warning("Concept %s not found in EDGAR data — skipping", concept_name)
            continue

        all_entries = extract_usd_units(concept)

        # Keep only quarterly and annual frames
        filtered = [e for e in all_entries if is_quarterly_frame(e)]

        # Deduplicate restatements: keep latest filed_date per (fy, fp)
        deduped = deduplicate(filtered)

        # Split into quarterly and annual maps
        quarterly, annual = build_quarterly_lookup(deduped)

        # Derive missing Q4 values
        quarterly = derive_q4(metric_name, quarterly, annual, missing_quarters)

        # Build output rows for Q1–Q4 only (not FY annual rows)
        for (fy, q), entry in sorted(quarterly.items()):
            if q not in (1, 2, 3, 4):
                continue
            output.append(
                {
                    "fiscal_year": fy,
                    "fiscal_quarter": q,
                    "metric": metric_name,
                    "value": entry["val"],
                    "filed_date": entry.get("filed", ""),
                }
            )

    # Print summary of missing quarters to stdout (Requirement 4.1)
    if missing_quarters:
        print("\n--- Missing fiscal quarters summary ---")
        for item in missing_quarters:
            print(f"  {item}")
        print("--- End missing fiscal quarters ---\n")
    else:
        print("No missing fiscal quarters detected.")

    return output


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    load_dotenv()

    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not user_agent:
        logger.error(
            "SEC_USER_AGENT environment variable is not set. "
            "Set it to 'CompanyName contact@example.com' as required by SEC EDGAR."
        )
        sys.exit(1)

    # Fetch
    company_facts = fetch_company_facts(EDGAR_URL, user_agent)

    # Extract
    records = extract_facts(company_facts)

    if not records:
        logger.error("No records extracted — check metric names and EDGAR response")
        sys.exit(1)

    logger.info("Extracted %d quarterly fact records", len(records))

    # Write output
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    logger.info("Written to %s", OUTPUT_PATH)


if __name__ == "__main__":
    main()
