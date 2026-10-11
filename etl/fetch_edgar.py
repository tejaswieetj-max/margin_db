"""
fetch_edgar.py — Fetch Microsoft quarterly financial facts from SEC EDGAR.

Fiscal year determination:
  Microsoft FY ends June 30. Fiscal year = calendar year of June 30 endpoint.
  Formula: If end_month >= 7: fiscal_year = calendar_year + 1
           If end_month <= 6: fiscal_year = calendar_year

Quarterly identification by DURATION (~days between start and end):
  75-100 days:   Single quarter (Q1, or explicit Q4 in 10-K)
  165-195 days:  Q2 (YTD cumulative: 6 months)
  255-285 days:  Q3 (YTD cumulative: 9 months)  
  350-380 days:  FY (annual)

Q4 derivation: Q4 = FY_annual - Q3_ytd (the 9-month YTD total)

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

EDGAR_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json"

METRIC_MAP = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
    "cogs": ["CostOfRevenue"],
    "operating_income": ["OperatingIncomeLoss"],
}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "data" / "raw" / "msft_facts.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logging.Formatter.converter = lambda *args: datetime.now(timezone.utc).timetuple()
logger = logging.getLogger(__name__)


def fetch_company_facts(url: str, user_agent: str) -> dict:
    logger.info("Fetching %s", url)
    try:
        response = requests.get(url, headers={"User-Agent": user_agent}, timeout=30)
    except requests.RequestException as exc:
        utc_now = datetime.now(timezone.utc).isoformat()
        logger.error("%s | %s | network error: %s", utc_now, url, exc)
        sys.exit(1)

    if response.status_code != 200:
        utc_now = datetime.now(timezone.utc).isoformat()
        logger.error("%s | %s | HTTP %s", utc_now, url, response.status_code)
        sys.exit(1)

    return response.json()


def get_fiscal_year_from_end_date(end_date_str: str) -> int:
    """Determine fiscal year from period end date.
    Microsoft FY ends June 30.
    If end_month >= 7: fiscal_year = calendar_year + 1
    If end_month <= 6: fiscal_year = calendar_year
    """
    try:
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
        return end_date.year + 1 if end_date.month >= 7 else end_date.year
    except:
        return None


def classify_period_by_duration(start: str, end: str) -> str:
    """Classify period by duration in days.
    75-100: Q1, 165-195: Q2, 255-285: Q3, 350-380: FY
    """
    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        duration = (end_dt - start_dt).days
        
        if 75 <= duration <= 100:
            # Single quarter - if June 30 end, it's Q4
            return "Q4" if end_dt.month == 6 and end_dt.day == 30 else "Q1"
        elif 165 <= duration <= 195:
            return "Q2"
        elif 255 <= duration <= 285:
            return "Q3"
        elif 350 <= duration <= 380:
            return "FY"
        return None
    except:
        return None


def extract_usd_units(concept_data: dict) -> list[dict]:
    return concept_data.get("units", {}).get("USD", [])


def deduplicate(entries: list[dict]) -> list[dict]:
    """Keep latest filed date per (fiscal_year, period_type)."""
    best: dict[tuple, dict] = {}
    for entry in entries:
        fy = entry.get("fiscal_year")
        pt = entry.get("period_type")
        if not fy or not pt:
            continue
        key = (fy, pt)
        filed = entry.get("filed", "")
        if key not in best or filed > best[key].get("filed", ""):
            best[key] = entry
    return list(best.values())


def extract_facts(company_facts: dict) -> list[dict]:
    us_gaap = company_facts.get("facts", {}).get("us-gaap", {})
    missing_quarters: list[str] = []
    output: list[dict] = []

    for metric_name, concept_names in METRIC_MAP.items():
        concept_data = None
        used_concept = None
        for concept_name in concept_names:
            concept_data = us_gaap.get(concept_name)
            if concept_data:
                used_concept = concept_name
                break
        
        if not concept_data:
            logger.warning("No concept for %s (tried %s)", metric_name, concept_names)
            continue
        
        logger.info("Using %s for %s", used_concept, metric_name)
        
        all_entries = extract_usd_units(concept_data)
        
        processed = []
        for entry in all_entries:
            start = entry.get("start", "")
            end = entry.get("end", "")
            if not end:
                continue
            
            fiscal_year = get_fiscal_year_from_end_date(end)
            if not fiscal_year:
                continue
            
            period_type = None
            if start:
                period_type = classify_period_by_duration(start, end)
            else:
                # No start - assume FY if 10-K
                period_type = "FY" if entry.get("form") == "10-K" else None
            
            if not period_type:
                continue
            
            processed.append({
                "fiscal_year": fiscal_year,
                "period_type": period_type,
                "val": entry["val"],
                "filed": entry.get("filed", ""),
                "end": end,
            })
        
        deduped = deduplicate(processed)
        
        lookup: dict[tuple[int, str], dict] = {}
        for entry in deduped:
            fy = entry["fiscal_year"]
            pt = entry["period_type"]
            lookup[(fy, pt)] = entry
        
        # Derive Q4
        all_fys = set(fy for (fy, _) in lookup.keys())
        for fy in sorted(all_fys):
            if (fy, "Q4") in lookup:
                continue
            
            ann = lookup.get((fy, "FY"))
            q3 = lookup.get((fy, "Q3"))
            
            if not ann:
                msg = f"metric={metric_name} FY{fy}: FY annual not available — Q4 skipped"
                logger.warning(msg)
                missing_quarters.append(msg)
                continue
            
            if not q3:
                msg = f"metric={metric_name} FY{fy}: Q3 not available — cannot derive Q4"
                logger.warning(msg)
                missing_quarters.append(msg)
                continue
            
            derived_val = ann["val"] - q3["val"]
            lookup[(fy, "Q4")] = {
                "fiscal_year": fy,
                "period_type": "Q4",
                "val": derived_val,
                "filed": ann["filed"],
                "end": ann["end"],
                "derived": True,
            }
            logger.debug("Derived Q4 for %s FY%d: %s - %s = %s", 
                        metric_name, fy, ann["val"], q3["val"], derived_val)
        
        quarter_map = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}
        for (fy, pt), entry in sorted(lookup.items()):
            q = quarter_map.get(pt)
            if q:
                output.append({
                    "fiscal_year": fy,
                    "fiscal_quarter": q,
                    "metric": metric_name,
                    "value": entry["val"],
                    "filed_date": entry.get("filed", ""),
                })
    
    if missing_quarters:
        print("\n--- Missing fiscal quarters ---")
        for item in missing_quarters:
            print(f"  {item}")
        print("--- End missing quarters ---\n")
    else:
        print("No missing fiscal quarters detected.")
    
    return output


def validate_output(records: list[dict]) -> None:
    """Validate FY24 Q2=62020, FY25 Q4=76441, FY26 Q2=81273."""
    validation_targets = [
        (2024, 2, 62020),
        (2025, 4, 76441),
        (2026, 2, 81273),
    ]
    
    print("\n--- Validation checks ---")
    for target_fy, target_q, expected_val in validation_targets:
        matches = [r for r in records 
                   if r["fiscal_year"] == target_fy 
                   and r["fiscal_quarter"] == target_q 
                   and r["metric"] == "revenue"]
        if matches:
            actual_val = matches[0]["value"]
            status = "✓" if actual_val == expected_val else "✗"
            print(f"{status} FY{target_fy} Q{target_q} revenue: expected ${expected_val:,}M, got ${actual_val:,}M")
        else:
            print(f"✗ FY{target_fy} Q{target_q} revenue: NOT FOUND")
    print("--- End validation ---\n")


def main() -> None:
    load_dotenv()

    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not user_agent:
        logger.error("SEC_USER_AGENT not set")
        sys.exit(1)

    company_facts = fetch_company_facts(EDGAR_URL, user_agent)
    records = extract_facts(company_facts)

    if not records:
        logger.error("No records extracted")
        sys.exit(1)

    logger.info("Extracted %d quarterly fact records", len(records))

    validate_output(records)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    logger.info("Written to %s", OUTPUT_PATH)


if __name__ == "__main__":
    main()
